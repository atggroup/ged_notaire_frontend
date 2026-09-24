"""Real document upload, encrypted storage, retrieval and workflow endpoints."""
import hashlib
import io
import json
import mimetypes
import secrets
import uuid
from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse
from django.utils import timezone
from rest_framework import parsers, permissions, status, serializers as drf_serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.models import User
from audit.services import log_event
from ged_backend.api import ContractSerializer
from ged_backend.confidentialite import est_plus_faible, plus_restrictif
from ged_backend.referentiels import DOMAINE_LABELS, NATURE_LABELS, ORIGINE_LABELS, QUALITE_PARTIE_LABELS, SUPPORT_LABELS, TYPE_DOCUMENT_LABELS
from dossiers.models import Dossier
from notifications.services import notify, notify_admins
from permissions_app.access import documents_visibles_par, dossiers_visibles_par, has_document_access
from .crypto import encrypt
from .models import Document, DocumentFavorite, SavedSearch
from .ocr import extract_text
from .serializers import contexte_liste, DocumentSerializer


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


MAGIC_TYPES = [(b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff")]


def actual_mime(data: bytes) -> str | None:
    return next((mime for signature, mime in MAGIC_TYPES if data.startswith(signature)), None)


# Les statuts qui composent la file de numérisation : ce qui attend encore
# une indexation / un contrôle qualité. Déclaré une seule fois pour que la
# liste de l'écran « Scanner » et le compteur du tableau de bord ne puissent
# plus diverger.
QUEUE_STATUSES = [Document.Status.TO_INDEX, Document.Status.TO_FIX]

# L'archivage est l'aboutissement du cycle de vie : il suppose une pièce
# validée (ou déjà archivée, pour rester idempotent).
ARCHIVABLE_STATUSES = [Document.Status.VALIDATED, Document.Status.ARCHIVED, Document.Status.CLOSED]


# Plafond de sécurité sur les listes : la réponse reste un tableau JSON (le
# front n'a rien à changer), mais le serveur ne tente plus de sérialiser
# l'intégralité d'un fonds documentaire. `limit`/`offset` permettent de
# parcourir au-delà.
LISTE_MAX = 200


def file_de_numerisation(user):
    """Ce qu'un utilisateur voit réellement dans la file de numérisation.

    Le rôle ouvre la file ; il n'ouvre pas les pièces. Sans ce filtrage, la
    file livrait à un clerc non habilité le nom ET le texte OCR extrait d'une
    pièce « Très confidentiel » que la route de détail lui refuse.

    S'y ajoute ce qu'il a lui-même déposé : on ne peut pas exiger de quelqu'un
    qu'il contrôle la qualité d'une numérisation qu'il vient de faire tout en
    la lui masquant. Le dépôt est déjà conditionné à l'accès au dossier
    (voir `upload_document`), donc cette ouverture ne crée aucun droit nouveau :
    elle rend visible une pièce que l'intéressé avait physiquement en main.
    Elle ne vaut d'ailleurs que tant que la pièce est DANS la file.

    Déclarée une seule fois pour que la liste de l'écran « Scanner » et le
    compteur du tableau de bord ne puissent jamais diverger.
    """
    en_attente = Document.objects.filter(statut__in=QUEUE_STATUSES)
    visibles = documents_visibles_par(user, en_attente).values_list("pk", flat=True)
    siennes = en_attente.filter(uploaded_by=user).values_list("pk", flat=True)
    return Document.objects.filter(pk__in=set(visibles) | set(siennes))


def borner(queryset, request):
    try:
        limite = min(int(request.query_params.get("limit", LISTE_MAX)), LISTE_MAX)
    except (TypeError, ValueError):
        limite = LISTE_MAX
    try:
        depart = max(int(request.query_params.get("offset", 0)), 0)
    except (TypeError, ValueError):
        depart = 0
    return list(queryset[depart:depart + limite])


def _nom_sur(nom: str) -> str:
    """Nom de fichier sûr pour l'en-tête Content-Disposition (guillemets,
    retours à la ligne et chemins neutralisés)."""
    import os
    propre = os.path.basename(str(nom).replace("\\", "/")).replace('"', "'")
    return "".join(c for c in propre if c.isprintable())[:180] or "document"


def document_or_404(reference: str):
    return Document.objects.filter(reference=reference).select_related("dossier", "uploaded_by", "validated_by").first()


def next_document_codes(dossier: Dossier | None, type_code: str) -> tuple[str, int]:
    """Generate a non-semantic technical identity and business sequence.

    La séquence vient d'un compteur verrouillé par dossier : `count() + 1`
    donnait la même séquence à deux dépôts simultanés."""
    from django.db.models import Max
    from core.coordination import next_sequence
    scope = f"document-seq:{dossier.pk if dossier else 'sans-dossier'}"
    sequence = next_sequence(scope, floor=lambda: Document.objects.filter(dossier=dossier).aggregate(m=Max("sequence"))["m"] or 0)
    return f"DOC_{uuid.uuid4().hex.upper()}", sequence


def _date_validite(request):
    """`valid_until` (AAAA-MM-JJ) optionnel ; renvoie (date, erreur)."""
    from django.utils.dateparse import parse_date
    brut = request.data.get("valid_until") or request.data.get("validUntil")
    if not brut:
        return None, None
    valeur = parse_date(str(brut))
    if valeur is None:
        return None, Response({"valid_until": ["Date de validité invalide (format AAAA-MM-JJ)."]}, status=400)
    return valeur, None


class DashboardSummaryView(APIView):
    def get(self, request):
        from audit.models import AuditLog
        from audit.services import security_alerts
        from dossiers.models import Dossier, DossierChecklistItem
        from notifications.models import Task
        from permissions_app.access import has_dossier_access
        from permissions_app.models import AccessRequest
        from .crypto import key_ring_status
        # Agrégats calculés PAR LA BASE : le tableau de bord chargeait jusqu'ici
        # toutes les pièces visibles en mémoire (texte OCR compris) à chaque
        # affichage — intenable au-delà de quelques milliers de pièces.
        visibles = documents_visibles_par(request.user, Document.objects.filter(is_archived=False))
        ids_visibles = visibles.values("pk")
        base_visible = Document.objects.filter(pk__in=ids_visibles)
        visible_count = base_visible.count()
        pending_count = base_visible.filter(statut=Document.Status.PENDING).count()
        recent = list(base_visible.select_related("dossier", "uploaded_by", "validated_by").defer("extracted_text").order_by("-created_at")[:4])
        visible_dossiers_count = base_visible.exclude(dossier__isnull=True).values("dossier_id").distinct().count()
        queue_count = file_de_numerisation(request.user).count()
        if request.user.role == "admin":
            # The notaire needs to see every request awaiting *their* decision,
            # not only ones they themselves happened to submit.
            pending_requests = AccessRequest.objects.filter(status=AccessRequest.Status.PENDING).select_related("requester", "document").order_by("-created_at")
        else:
            pending_requests = AccessRequest.objects.filter(requester=request.user, status=AccessRequest.Status.PENDING).select_related("document").order_by("-created_at")
        access_requests = pending_requests.count()
        consultations = AuditLog.objects.filter(user=request.user, action="document_viewed").count()

        # "Ajouter un tableau de bord par rôle" (cadrage §21) : au-delà des
        # compteurs génériques ci-dessus, chaque rôle voit les signaux qui le
        # concernent réellement — dossiers sensibles/gelés, pièces
        # manquantes, échéances, et pour le notaire des alertes de sécurité.
        open_statuses = [Dossier.Status.OPEN, Dossier.Status.IN_PROGRESS, Dossier.Status.WAITING, Dossier.Status.READY]
        visible_dossiers = list(dossiers_visibles_par(request.user, Dossier.objects.filter(statut__in=open_statuses)))
        dossiers_sensibles = [d for d in visible_dossiers if d.niveau_de_confidentialite in {Dossier.Confidentiality.CONFIDENTIAL, Dossier.Confidentiality.VERY_CONFIDENTIAL}]
        dossiers_bloques = [d for d in visible_dossiers if d.legal_hold]
        pieces_manquantes = DossierChecklistItem.objects.filter(dossier__in=[d.pk for d in visible_dossiers], required=True, completed_at__isnull=True).select_related("dossier")
        echeances = Task.objects.filter(assigned_to=request.user, status__in=[Task.Status.OPEN, Task.Status.IN_PROGRESS], due_at__isnull=False).order_by("due_at")[:10]

        summary = {
            "documentsArchivedCount": Document.objects.filter(is_archived=True).count(),
            "documentsAccessibleCount": visible_count,
            "dossiersCount": visible_dossiers_count if request.user.role != "admin" else Document.objects.filter(is_archived=False, dossier__isnull=False).values("dossier_id").distinct().count(),
            "activeUsersCount": User.objects.filter(is_active=True).count(),
            "pendingValidationCount": pending_count,
            "pendingIndexationCount": queue_count,
            "pendingAccessRequestCount": access_requests,
            "consultationsCount": consultations,
            "pendingAccessRequests": [{"reference": item.document.reference if item.document_id else "—", "status": item.get_status_display(), "createdAt": item.created_at.isoformat(), "demandeur": item.requester.display_name if request.user.role == "admin" else None} for item in pending_requests[:3]],
            "recentDocuments": DocumentSerializer(recent, many=True, context=contexte_liste(request, recent)).data,
            "dossiersSensibles": [{"reference": d.reference, "nom": d.nom, "niveau": d.niveau_de_confidentialite} for d in dossiers_sensibles[:10]],
            "dossiersSensiblesCount": len(dossiers_sensibles),
            "dossiersBloques": [{"reference": d.reference, "nom": d.nom, "motif": d.legal_hold_reason} for d in dossiers_bloques[:10]],
            "dossiersBloquesCount": len(dossiers_bloques),
            "piecesManquantes": [{"dossier": item.dossier.reference, "libelle": item.label} for item in pieces_manquantes[:10]],
            "piecesManquantesCount": pieces_manquantes.count(),
            "echeances": [{"id": t.id, "titre": t.title, "dossier": t.dossier.reference if t.dossier_id else None, "echeance": t.due_at.isoformat(), "priorite": t.priority} for t in echeances],
        }
        summary["dossiersIncompletsCount"] = len({item.dossier_id for item in pieces_manquantes})
        summary["piecesARecevoirCount"] = pieces_manquantes.filter(document__isnull=False).count()
        if request.user.role in {"admin", "clerc"}:
            file_ocr = Document.objects.exclude(statut=Document.Status.DESTROYED)
            summary["ocr"] = {
                "enAttente": file_ocr.filter(ocr_status__in=[Document.OCRStatus.PENDING, Document.OCRStatus.PROCESSING]).count(),
                "echecs": file_ocr.filter(ocr_status=Document.OCRStatus.FAILED, is_current=True).count(),
            }
        if request.user.role == "admin":
            from audit.models import SecurityAlert
            from core.models import JobRun
            from core.views import worker_status
            from settings_app.backup_service import backup_freshness
            summary["alertesSecurite"] = security_alerts()
            summary["chiffrement"] = key_ring_status()
            summary["alertesOuvertes"] = [{"id": a.pk, "titre": a.title, "gravite": a.severity, "date": a.created_at.isoformat()}
                                          for a in SecurityAlert.objects.filter(status=SecurityAlert.Status.OPEN)[:5]]
            summary["alertesOuvertesCount"] = SecurityAlert.objects.filter(status=SecurityAlert.Status.OPEN).count()
            fraicheur = backup_freshness()
            dernieres = {}
            for run in JobRun.objects.exclude(status__in=[JobRun.Status.REQUESTED, JobRun.Status.RUNNING]).order_by("-started_at")[:300]:
                dernieres.setdefault(run.name, run.status)
            summary["automatisations"] = {
                "travailleurs": worker_status(),
                "travauxEnEchec": sorted(nom for nom, statut in dernieres.items() if statut == JobRun.Status.FAILURE),
                "sauvegardeAJour": fraicheur["ok"],
            }
        return Response(summary)


class ReferentielsView(APIView):
    """Cadrage §4-9 : référentiels fermés exposés pour peupler les listes déroulantes."""
    def get(self, request):
        from ged_backend.referentiels import DOMAINES, NATURES, ORIGINES, QUALITES_PARTIES, STATUTS, SUPPORTS, TYPES_DOCUMENTS_GROUPES
        return Response({
            "domaines": [{"code": c, "label": l} for c, l in DOMAINES],
            "typesDocuments": [{"categorie": cat, "options": [{"code": c, "label": l} for c, l in items]} for cat, items in TYPES_DOCUMENTS_GROUPES],
            "statuts": [{"code": c, "label": l} for c, l in STATUTS],
            "origines": [{"code": c, "label": l} for c, l in ORIGINES],
            "supports": [{"code": c, "label": l} for c, l in SUPPORTS],
            "natures": [{"code": c, "label": l} for c, l in NATURES],
            "qualitesParties": [{"code": c, "label": l} for c, l in QUALITES_PARTIES],
        })


class DocumentsView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser, parsers.JSONParser]

    def get(self, request):
        docs = Document.objects.filter(is_archived=False, trashed_at__isnull=True, is_current=True)
        type_ = request.query_params.get("type")
        level = request.query_params.get("niveau")
        if type_ and type_ != "Tous": docs = docs.filter(type__iexact=type_)
        if level and level != "Tous": docs = docs.filter(niveau_de_confidentialite=level)
        allowed = borner(documents_visibles_par(request.user, docs.select_related("dossier", "uploaded_by", "validated_by").defer("extracted_text")), request)
        return Response(DocumentSerializer(allowed, many=True, context=contexte_liste(request, allowed)).data)

    def post(self, request):
        return upload_document(request)


class UploadDocumentView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    def post(self, request):
        return upload_document(request)


def upload_document(request):
    if request.user.role not in {"admin", "clerc"} and not (request.user.role == "collaborateur" and request.user.can_scan):
        return Response({"detail": "Votre rôle ne peut pas numériser de document."}, status=403)
    uploaded = request.FILES.get("fichier") or request.FILES.get("file") or request.FILES.get("document")
    if not uploaded:
        return Response({"fichier": ["Un fichier multipart est obligatoire (champ fichier, file ou document)."]}, status=400)
    if uploaded.size > settings.DOCUMENT_MAX_UPLOAD_BYTES:
        return Response({"fichier": ["Le fichier dépasse la taille maximale autorisée."]}, status=400)
    raw = uploaded.read()
    from .formats import FormatRefuse, identifier
    try:
        mime = identifier(raw)
    except FormatRefuse as exc:
        return Response({"fichier": [str(exc)]}, status=400)
    type_ = request.data.get("type") or request.data.get("type_d_acte")
    type_code = request.data.get("type_code") or request.data.get("typeCode")
    if type_code and type_code not in TYPE_DOCUMENT_LABELS:
        return Response({"type_code": ["Code type invalide. Choisissez un code du référentiel (§5)."]}, status=400)
    if not type_ and type_code:
        type_ = TYPE_DOCUMENT_LABELS[type_code]
    if not type_:
        return Response({"type": ["Le type d'acte est obligatoire."]}, status=400)
    dossier_ref = request.data.get("dossier") or request.data.get("dossierReference")
    # A business reference such as VEN-2026-00001 is not a numeric primary
    # key.  Avoid building an invalid ``pk=<reference>`` ORM predicate.
    dossier = Dossier.objects.filter(reference=dossier_ref).first() if dossier_ref else None
    if dossier is None and dossier_ref and str(dossier_ref).isdigit():
        dossier = Dossier.objects.filter(pk=int(dossier_ref)).first()
    if dossier_ref and not dossier:
        return Response({"dossier": ["Dossier introuvable."]}, status=400)
    if not dossier:
        return Response({"dossier": ["Un dossier existant est obligatoire. Créez l'affaire avant de numériser ; un client peut avoir plusieurs dossiers."]}, status=400)
    # Déposer dans un dossier, c'est agir dessus : rien ne vérifiait l'accès au
    # dossier visé. Un clerc pouvait glisser une pièce dans une affaire
    # confidentielle qu'il n'a pas le droit d'ouvrir — et fausser au passage
    # son inventaire et sa checklist.
    from permissions_app.access import has_dossier_access
    if not has_dossier_access(request.user, dossier):
        # Formulation volontairement indistincte du « dossier inexistant » :
        # elle n'apprend rien à qui tente une référence au hasard, et dit à
        # l'utilisateur légitime quoi faire.
        return Response({"dossier": ["Dossier introuvable, ou vous n'y êtes pas affecté. Demandez au notaire de vous affecter à cette affaire."]}, status=400)
    if dossier.legal_hold:
        return Response({"detail": "Ce dossier est gelé : aucun nouveau document ne peut y être déposé."}, status=409)
    requested_level = request.data.get("niveau_de_confidentialite", request.data.get("niveau", "Standard"))
    # Ignore rather than trust any confidentiality value sent by a clerc.
    if request.user.role != "admin":
        requested_level = "Standard"
    if requested_level not in Document.Confidentiality.values:
        return Response({"niveau_de_confidentialite": ["Valeur invalide."]}, status=400)
    origine = request.data.get("origine") or "CLI"
    support = request.data.get("support") or "SCN"
    nature = request.data.get("nature") or "SCN"
    qualite_partie = request.data.get("qualite_partie") or ""
    for value, labels, field in ((origine, ORIGINE_LABELS, "origine"), (support, SUPPORT_LABELS, "support"), (nature, NATURE_LABELS, "nature")):
        if value not in labels:
            return Response({field: ["Valeur invalide."]}, status=400)
    if qualite_partie and qualite_partie not in QUALITE_PARTIE_LABELS:
        return Response({"qualite_partie": ["Valeur invalide."]}, status=400)
    valid_until, erreur = _date_validite(request)
    if erreur is not None:
        return erreur
    # Analyse antivirus AVANT tout enregistrement (dernière étape, après les
    # contrôles bon marché : type réel, droits, dossier).
    from .antivirus import controler_depot
    refus = controler_depot(request, raw, uploaded.name)
    if refus is not None:
        return refus
    empreinte = hashlib.sha256(raw).hexdigest()
    try:
        with transaction.atomic():
            # The case policy is the default. A notaire may add a more
            # restrictive documentary exception, never a weaker one : le niveau
            # du dossier est un plancher, jamais une simple valeur par défaut.
            level = plus_restrictif(requested_level, dossier.niveau_de_confidentialite)
            reference, sequence = next_document_codes(dossier, type_code or "DOC")
            encrypted, key_id = encrypt(raw)
            doc = Document(
                reference=reference, sequence=sequence, dossier=dossier, type=type_, type_code=type_code or "",
                origine=origine, support=support, nature=nature, qualite_partie=qualite_partie,
                nom=request.data.get("nom") or uploaded.name, original_filename=uploaded.name, content_type=mime,
                size_bytes=len(raw), sha256=empreinte, encryption_key_id=key_id, niveau_de_confidentialite=level,
                master_reference=reference, statut=Document.Status.TO_INDEX,
                uploaded_by=request.user, valid_until=valid_until,
            )
            doc.fichier.save(uploaded.name, ContentFile(encrypted), save=False)
            doc.save()
            doc.code_notarial = doc.code_notarial_actuel()
            doc.save(update_fields=["code_notarial"])
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=503)
    # L'OCR ne tourne PLUS dans la requête. Rasteriser 50 pages à 300 dpi
    # occupait un fil d'exécution plusieurs minutes — gunicorn n'en offre que
    # six et nginx coupe à 180 s : quelques numérisations simultanées
    # suffisaient à figer l'étude, et un gros acte renvoyait une erreur au
    # déposant alors que la pièce était bien enregistrée.
    # La pièce reste donc « OCR en attente » et la commande périodique
    # `traiter_ocr` s'en charge (le bouton « Lancer l'OCR » reste disponible).
    # Doublon : le même fichier (même empreinte) figure déjà dans le dossier.
    # On ne refuse pas — un original et sa copie certifiée peuvent coïncider —
    # mais on le dit, et c'est tracé.
    doublons = list(Document.objects.filter(dossier=dossier, sha256=empreinte, is_current=True)
                    .exclude(pk=doc.pk).exclude(statut=Document.Status.DESTROYED).values_list("reference", flat=True)[:5])
    from dossiers.checklist import rapprocher_piece
    item = rapprocher_piece(doc)
    log_event(request, "document_uploaded", "document", doc.reference, metadata={
        "sha256": doc.sha256, "content_type": mime, "ocr_status": doc.ocr_status,
        **({"doublon_de": doublons} if doublons else {}), **({"checklist_item": item.pk} if item else {})})
    notify_admins("document", "Document à contrôler", f"{request.user.display_name} a déposé « {doc.nom} » ({doc.reference}). Contrôle qualité requis.",
                  exclude=request.user if request.user.role == "admin" else None, target_type="document", target_id=doc.reference)
    data = DocumentSerializer(doc, context={"request": request}).data
    avertissements = []
    if doublons:
        avertissements.append(f"Ce fichier est identique à une pièce déjà présente dans le dossier ({', '.join(doublons)}).")
    if item:
        avertissements.append(f"Pièce rattachée à la checklist : « {item.label} » (à vérifier puis cocher).")
    data["warnings"] = avertissements
    data["checklistItem"] = item.pk if item else None
    return Response(data, status=status.HTTP_201_CREATED)


class DocumentDetailView(APIView):
    def get(self, request, reference):
        doc = document_or_404(reference)
        if not doc: return Response({"detail": "Document introuvable."}, status=404)
        if not has_document_access(request.user, doc): return Response({"detail": "Accès refusé."}, status=403)
        log_event(request, "document_viewed", "document", doc.reference)
        if request.query_params.get("format") == "json" or "application/json" in request.headers.get("Accept", ""):
            return Response(DocumentSerializer(doc, context={"request": request}).data)
        try:
            response = FileResponse(io.BytesIO(doc.decrypted_bytes()), content_type=doc.content_type)
        except ValueError:
            return Response({"detail": "Fichier chiffré illisible."}, status=500)
        from .formats import MIMES_BUREAUTIQUES
        disposition = "attachment" if doc.content_type in MIMES_BUREAUTIQUES else "inline"
        response["Content-Disposition"] = f'{disposition}; filename="{_nom_sur(doc.original_filename)}"'
        return response

    def patch(self, request, reference):
        # Le notaire ajoute ou lève une restriction sur CE document précis,
        # sans toucher aux autres documents du dossier (contrairement à la
        # confidentialité posée au niveau du dossier, qui se propage).
        doc = document_or_404(reference)
        if not doc: return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role != "admin": return Response({"detail": "Seul le notaire peut modifier la confidentialité d'un document."}, status=403)
        if doc.dossier and doc.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : la confidentialité de ses documents ne peut pas être modifiée."}, status=409)
        level = request.data.get("niveau_de_confidentialite", request.data.get("niveau"))
        if not level: return Response({"detail": "Niveau de confidentialité requis."}, status=400)
        if level not in Document.Confidentiality.values:
            return Response({"niveau_de_confidentialite": ["Valeur invalide."]}, status=400)
        if doc.dossier and est_plus_faible(level, doc.dossier.niveau_de_confidentialite):
            return Response({"niveau_de_confidentialite": [
                f"Le dossier est classé « {doc.dossier.niveau_de_confidentialite} » : "
                f"une pièce ne peut pas être ramenée à « {level} ». "
                "Abaissez d'abord la confidentialité du dossier."
            ]}, status=409)
        previous = doc.niveau_de_confidentialite
        doc.niveau_de_confidentialite = level
        doc.save(update_fields=["niveau_de_confidentialite", "updated_at"])
        log_event(request, "document_restriction_updated", "document", doc.reference, metadata={"previous": previous, "next": level})
        if level == Document.Confidentiality.STANDARD and previous != Document.Confidentiality.STANDARD:
            notify_admins("document", "Restriction levée", f"{request.user.display_name} a levé la restriction sur « {doc.nom} » ({doc.reference}).", exclude=request.user)
        return Response(DocumentSerializer(doc, context={"request": request}).data)


class ExportDocumentView(APIView):
    def get(self, request, reference):
        doc = document_or_404(reference)
        if not doc: return Response({"detail": "Document introuvable."}, status=404)
        if not has_document_access(request.user, doc): return Response({"detail": "Accès refusé."}, status=403)
        log_event(request, "document_exported", "document", doc.reference)
        response = FileResponse(io.BytesIO(doc.decrypted_bytes()), content_type=doc.content_type)
        response["Content-Disposition"] = f'attachment; filename="{_nom_sur(doc.original_filename)}"'
        return response


class ValidateDocumentView(APIView):
    def post(self, request, reference):
        doc = document_or_404(reference)
        if not doc: return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role != "admin": return Response({"detail": "Validation réservée au notaire."}, status=403)
        if not doc.is_current:
            return Response({"detail": "Une ancienne version ne peut pas être validée."}, status=409)
        if not doc.quality_passed:
            return Response({"detail": "Le contrôle qualité doit être validé avant la validation notariale."}, status=409)
        # La validation n'est ouverte qu'à une pièce qui l'attend : une pièce
        # en corbeille ou déjà archivée ne doit pas pouvoir redevenir « validé ».
        if doc.trashed_at or doc.statut != Document.Status.PENDING:
            return Response({"detail": f"Seule une pièce « En validation » peut être validée (statut actuel : « {doc.get_statut_display()} »)."}, status=409)
        doc.statut = Document.Status.VALIDATED
        doc.validated_by = request.user
        doc.validated_at = timezone.now()
        doc.save(update_fields=["statut", "validated_by", "validated_at", "updated_at"])
        log_event(request, "document_validated", "document", doc.reference)
        if doc.uploaded_by_id != request.user.id:
            notify(
                doc.uploaded_by,
                "document",
                "Document validé",
                f"« {doc.nom} » ({doc.reference}) a été validé par {request.user.display_name}.",
            )
        return Response(DocumentSerializer(doc, context={"request": request}).data)


class ArchiveDocumentView(APIView):
    def post(self, request):
        ref = request.data.get("ref") or request.data.get("reference")
        doc = document_or_404(ref) if ref else None
        if not doc: return Response({"detail": "Référence de document obligatoire."}, status=400)
        if request.user.role != "admin": return Response({"detail": "Archivage réservé au notaire."}, status=403)
        # Les garde-fous d'abord, la mutation ensuite : l'état de l'objet ne
        # doit jamais être modifié sur un chemin qui va être refusé.
        if doc.dossier and doc.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : l'archivage ou la purge est bloqué."}, status=409)
        if doc.trashed_at:
            return Response({"detail": "Un document en corbeille doit d'abord être restauré avant d'être archivé."}, status=409)
        # L'archivage clôt le cycle de vie : on n'archive que ce qui a été
        # contrôlé puis validé. Archiver un brouillon figeait jusqu'ici une
        # pièce qui n'avait franchi aucune étape de contrôle.
        if doc.statut not in ARCHIVABLE_STATUSES:
            return Response({"detail": (
                "Seul un document validé peut être archivé "
                f"(statut actuel : « {doc.get_statut_display()} »). "
                "Effectuez le contrôle qualité puis la validation notariale."
            )}, status=409)
        doc.is_archived = True
        doc.statut = Document.Status.ARCHIVED
        doc.save(update_fields=["is_archived", "statut", "updated_at"])
        log_event(request, "document_archived", "document", doc.reference)
        if doc.uploaded_by_id != request.user.id:
            notify(
                doc.uploaded_by,
                "document",
                "Document archivé",
                f"« {doc.nom} » ({doc.reference}) a été archivé par {request.user.display_name}.",
            )
        return Response({"ok": True, "reference": doc.reference})


class QualityCheckView(APIView):
    """Records the human scan review: completeness, order, legibility,
    missing/duplicate pages, orientation and case association."""
    def post(self, request, reference):
        doc = document_or_404(reference)
        if not doc:
            return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Contrôle réservé au personnel habilité."}, status=403)
        # Refaire un contrôle qualité sur un acte validé le ramenait en
        # « en_validation » : sa validation notariale disparaissait sans bruit.
        if doc.trashed_at or doc.statut not in {*QUEUE_STATUSES, Document.Status.PENDING}:
            return Response({"detail": f"Le contrôle qualité ne s'applique qu'aux pièces à indexer, à corriger ou en validation (statut actuel : « {doc.get_statut_display()} »)."}, status=409)
        checks = request.data.get("checks", {})
        required = {"complete", "ordered", "legible", "noMissingPage", "noDuplicate", "orientationCorrect", "dossierCorrect"}
        missing = sorted(required - set(checks))
        if missing:
            return Response({"checks": [f"Contrôles obligatoires manquants : {', '.join(missing)}."]}, status=400)
        passed = all(bool(checks[key]) for key in required)
        doc.quality_checked_by = request.user
        doc.quality_checked_at = timezone.now()
        doc.quality_passed = passed
        doc.quality_notes = str(request.data.get("notes", ""))
        doc.statut = Document.Status.PENDING if passed else Document.Status.TO_FIX
        doc.save(update_fields=["quality_checked_by", "quality_checked_at", "quality_passed", "quality_notes", "statut", "updated_at"])
        log_event(request, "document_quality_checked", "document", doc.reference, result="success" if passed else "failure", metadata={"checks": checks})
        if passed:
            notify_admins("document_to_validate", "Document à valider",
                          f"« {doc.nom} » ({doc.reference}) a passé le contrôle qualité ({request.user.display_name}) : validation notariale attendue.",
                          exclude=request.user, target_type="document", target_id=doc.reference)
        elif doc.uploaded_by_id != request.user.pk:
            notify(doc.uploaded_by, "document_to_fix", "Document à corriger",
                   f"« {doc.nom} » ({doc.reference}) n'a pas passé le contrôle qualité. "
                   + (f"Motif : {doc.quality_notes[:300]}" if doc.quality_notes else "Déposez une version corrigée."),
                   severity="attention", target_type="document", target_id=doc.reference)
        return Response(DocumentSerializer(doc, context={"request": request}).data)


def process_ocr(doc: Document) -> Document:
    """Extract searchable content without altering the preserved binary."""
    try:
        text, state = extract_text(doc.decrypted_bytes(), doc.content_type)
        doc.extracted_text = text
        doc.ocr_status = Document.OCRStatus.EXTRACTED if state == "extrait" else Document.OCRStatus.UNAVAILABLE
        doc.ocr_error = ""
    except Exception as exc:  # OCR failures must never discard a source scan.
        doc.ocr_status = Document.OCRStatus.FAILED
        doc.ocr_error = str(exc)[:1000]
    doc.ocr_processed_at = timezone.now()
    doc.save(update_fields=["extracted_text", "ocr_status", "ocr_error", "ocr_processed_at", "updated_at"])
    return doc


class OCRDocumentView(APIView):
    def post(self, request, reference):
        doc = document_or_404(reference)
        if not doc or not has_document_access(request.user, doc):
            return Response({"detail": "Document introuvable ou accès refusé."}, status=404)
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Relance OCR réservée au personnel habilité."}, status=403)
        process_ocr(doc)
        log_event(request, "document_ocr_processed", "document", doc.reference, metadata={"status": doc.ocr_status, "characters": len(doc.extracted_text)})
        return Response(DocumentSerializer(doc, context={"request": request}).data)


class DocumentVersionView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def get(self, request, reference):
        doc = document_or_404(reference)
        if not doc or not has_document_access(request.user, doc):
            return Response({"detail": "Document introuvable ou accès refusé."}, status=404)
        versions = Document.objects.filter(master_reference=doc.id_maitre).order_by("version")
        return Response(DocumentSerializer(versions, many=True, context=contexte_liste(request, versions)).data)

    def post(self, request, reference):
        current = document_or_404(reference)
        if not current:
            return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role not in {"admin", "clerc"} or not has_document_access(request.user, current):
            return Response({"detail": "Accès refusé."}, status=403)
        if current.dossier and current.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : aucune nouvelle version ne peut être déposée."}, status=409)
        uploaded = request.FILES.get("fichier") or request.FILES.get("file") or request.FILES.get("document")
        if not uploaded:
            return Response({"fichier": ["Le nouveau fichier est obligatoire."]}, status=400)
        if uploaded.size > settings.DOCUMENT_MAX_UPLOAD_BYTES:
            return Response({"fichier": ["Le fichier dépasse la taille maximale autorisée."]}, status=400)
        raw = uploaded.read()
        from .formats import FormatRefuse, identifier
        try:
            mime = identifier(raw)
        except FormatRefuse as exc:
            return Response({"fichier": [str(exc)]}, status=400)
        valid_until, erreur = _date_validite(request)
        if erreur is not None:
            return erreur
        from .antivirus import controler_depot
        refus = controler_depot(request, raw, uploaded.name)
        if refus is not None:
            return refus
        with transaction.atomic():
            # Verrou sur la version courante : deux dépôts simultanés
            # produisaient deux versions « courantes » du même acte, et
            # versionner une ancienne version créait une branche parallèle.
            current = Document.objects.select_for_update().select_related("dossier").get(pk=current.pk)
            if not current.is_current:
                return Response({"detail": "Cette pièce a déjà été remplacée : déposez la nouvelle version sur la version courante."}, status=409)
            # Never overwrite a validated binary: a correction is a fresh,
            # immutable version linked to the same technical identity.
            current.is_current = False
            current.save(update_fields=["is_current", "updated_at"])
            new_ref = f"DOC_{uuid.uuid4().hex.upper()}"
            new_encrypted, new_key_id = encrypt(raw)
            new_doc = Document(
                reference=new_ref, master_reference=current.id_maitre, previous_version=current,
                is_current=True, version=current.version + 1, sequence=current.sequence,
                dossier=current.dossier, type=current.type, type_code=current.type_code,
                origine=current.origine, support=current.support, nature=current.nature,
                qualite_partie=current.qualite_partie, nom=request.data.get("nom") or current.nom,
                original_filename=uploaded.name, content_type=mime, size_bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(), encryption_key_id=new_key_id, niveau_de_confidentialite=current.niveau_de_confidentialite,
                statut=Document.Status.TO_INDEX, uploaded_by=request.user,
                valid_until=valid_until or current.valid_until,
            )
            new_doc.fichier.save(uploaded.name, ContentFile(new_encrypted), save=False)
            new_doc.save()
            new_doc.code_notarial = new_doc.code_notarial_actuel()
            new_doc.save(update_fields=["code_notarial"])
        # La pièce rattachée à la checklist suit sa nouvelle version.
        from dossiers.models import DossierChecklistItem
        DossierChecklistItem.objects.filter(document=current).update(document=new_doc)
        log_event(request, "document_new_version", "document", new_doc.reference, metadata={"previous": current.reference, "sha256": new_doc.sha256})
        _notifier_nouvelle_version(request, current, new_doc)
        return Response(DocumentSerializer(new_doc, context={"request": request}).data, status=status.HTTP_201_CREATED)


def _notifier_nouvelle_version(request, ancienne: Document, nouvelle: Document) -> None:
    """Les personnes affectées au dossier (qui ont accès à la pièce) et les
    notaires apprennent qu'une version remplace celle qu'ils connaissaient."""
    from dossiers.models import DossierAssignment
    from notifications.services import active_admins, emit
    destinataires = []
    if nouvelle.dossier_id:
        for affectation in DossierAssignment.objects.filter(dossier_id=nouvelle.dossier_id).select_related("user"):
            if affectation.user_id != request.user.pk and has_document_access(affectation.user, nouvelle):
                destinataires.append(affectation.user)
    destinataires += active_admins(exclude=request.user)
    revalidation = ""
    if ancienne.statut in {Document.Status.VALIDATED, Document.Status.ARCHIVED}:
        revalidation = " L'ancienne version était validée : la nouvelle doit repasser le contrôle et la validation."
    emit(destinataires, "document_new_version", "Nouvelle version déposée",
         f"{request.user.display_name} a déposé la version {nouvelle.version} de « {nouvelle.nom} » ({nouvelle.reference}).{revalidation}",
         target_type="document", target_id=nouvelle.reference)


class FavoriteDocumentView(APIView):
    def post(self, request):
        doc = document_or_404(request.data.get("ref"))
        # Même réponse qu'une référence inexistante : mettre en favori une
        # pièce interdite renvoyait 200 et confirmait son existence.
        if not doc or not has_document_access(request.user, doc):
            return Response({"detail": "Document introuvable."}, status=404)
        favorite, created = DocumentFavorite.objects.get_or_create(user=request.user, document=doc)
        if not created: favorite.delete()
        return Response({"ok": True, "is_favorite": created})


class DocumentTrashView(APIView):
    """A recoverable deletion workflow.  Bytes are never destroyed here."""

    ACTIONS = ("trash", "restore", "request-destruction", "authorize-destruction", "destroy")

    def post(self, request, reference, action):
        # Le routeur accepte n'importe quel segment : on valide ici, sinon une
        # action inconnue traversait la cascade de `elif` et faisait éclater la
        # vue sur une variable `event` jamais assignée (HTTP 500).
        if action not in self.ACTIONS:
            return Response(
                {"detail": f"Action inconnue. Actions possibles : {', '.join(self.ACTIONS)}."},
                status=404,
            )
        doc = document_or_404(reference)
        if not doc:
            return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role != "admin":
            return Response({"detail": "La corbeille documentaire est réservée au notaire."}, status=403)
        if doc.dossier and doc.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : aucune destruction ou mise à la corbeille n'est autorisée."}, status=409)
        now = timezone.now()
        metadata: dict = {}
        if action == "trash":
            if doc.trashed_at:
                return Response({"detail": "Document déjà dans la corbeille."}, status=409)
            # On retient le statut courant pour pouvoir le rendre à l'identique.
            doc.statut_avant_corbeille = doc.statut
            doc.trashed_at, doc.trashed_by, doc.statut = now, request.user, Document.Status.TRASHED
            event = "document_trashed"
        elif action == "restore":
            if not doc.trashed_at:
                return Response({"detail": "Ce document n'est pas dans la corbeille."}, status=409)
            doc.trashed_at, doc.trashed_by = None, None
            doc.destruction_requested_at = doc.destruction_requested_by = None
            doc.destruction_authorized_at = doc.destruction_authorized_by = None
            # Le document retrouve réellement le statut qu'il avait — c'est ce
            # que promet la fenêtre de confirmation côté écran. Sans mémoire
            # (pièces mises à la corbeille avant cette correction), on retombe
            # sur l'ancien comportement.
            doc.statut = doc.statut_avant_corbeille or (
                Document.Status.ARCHIVED if doc.is_archived else Document.Status.TO_INDEX
            )
            metadata["restored_to"] = doc.statut
            doc.statut_avant_corbeille = ""
            event = "document_restored"
        elif action == "request-destruction":
            if not doc.trashed_at:
                return Response({"detail": "Placez d'abord le document dans la corbeille."}, status=409)
            motif = str(request.data.get("motif", "")).strip()
            if not motif:
                return Response({"motif": ["Le motif de la demande de destruction est obligatoire."]}, status=400)
            doc.destruction_requested_at, doc.destruction_requested_by = now, request.user
            doc.statut, event = Document.Status.DESTRUCTION_REQUESTED, "document_destruction_requested"
            metadata["motif"] = motif
        elif action == "authorize-destruction":
            if not doc.destruction_requested_at:
                return Response({"detail": "Aucune demande de destruction n'est en attente."}, status=409)
            # Séparation des tâches : détruire une minute est irréversible, la
            # demande et l'autorisation ne peuvent pas émaner de la même
            # personne dès lors que l'étude compte un second notaire habilité.
            autre_notaire_disponible = User.objects.filter(
                role=User.Role.ADMIN, is_active=True
            ).exclude(pk=doc.destruction_requested_by_id).exists()
            if doc.destruction_requested_by_id == request.user.pk and autre_notaire_disponible:
                return Response(
                    {"detail": "La destruction doit être autorisée par un notaire autre que le demandeur."},
                    status=409,
                )
            # Étude à notaire unique : l'opération reste possible, mais le
            # journal d'audit dit explicitement que le contrôle croisé n'a pas
            # pu s'appliquer — l'écart est tracé, jamais silencieux.
            metadata["separation_des_taches"] = doc.destruction_requested_by_id != request.user.pk
            doc.destruction_authorized_at, doc.destruction_authorized_by = now, request.user
            doc.statut, event = Document.Status.DESTRUCTION_AUTHORIZED, "document_destruction_authorized"
        elif action == "destroy":
            if not doc.destruction_authorized_at:
                return Response({"detail": "La destruction doit d'abord être autorisée."}, status=409)
            # (le gel est déjà vérifié en tête de vue, avant le if/elif)
            if doc.fichier:
                storage = doc.fichier.storage
                name = doc.fichier.name
                if name and storage.exists(name):
                    storage.delete(name)
            doc.fichier = ""
            doc.destroyed_at, doc.destroyed_by = now, request.user
            doc.statut = Document.Status.DESTROYED
            event = "document_destroyed"
        doc.save()
        log_event(request, event, "document", doc.reference, metadata=metadata)
        return Response(DocumentSerializer(doc, context={"request": request}).data)


class SearchView(APIView):
    def get(self, request):
        q = request.query_params.get("q", "").strip()
        type_ = request.query_params.get("type", "")
        dossier = request.query_params.get("dossier", "").strip()
        # `is_current=True` comme la liste des documents : sans lui, la
        # recherche remontait les versions remplacées et un clerc pouvait
        # travailler de bonne foi sur une pièce périmée.
        docs = Document.objects.filter(is_archived=False, trashed_at__isnull=True, is_current=True)
        if q:
            docs = docs.filter(Q(reference__icontains=q) | Q(code_notarial__icontains=q) | Q(nom__icontains=q) | Q(type__icontains=q) | Q(extracted_text__icontains=q) | Q(dossier__reference__icontains=q))
        if type_: docs = docs.filter(type__iexact=type_)
        if dossier:
            docs = docs.filter(Q(dossier__reference__icontains=dossier) | Q(dossier__client__icontains=dossier) | Q(dossier__nom__icontains=dossier))
        # Multi-criteria search: values are normal ORM filters, never raw SQL.
        for field, lookup in (("client", "dossier__client__icontains"), ("partie", "dossier__parties__client__nom__icontains"), ("reference", "reference__icontains"), ("statut", "statut"), ("niveau", "niveau_de_confidentialite"), ("domaine", "dossier__domaine")):
            value = request.query_params.get(field, "").strip()
            if value:
                docs = docs.filter(**{lookup: value})
        if request.query_params.get("responsable"):
            docs = docs.filter(dossier__assignments__user_id=request.query_params["responsable"])
        if request.query_params.get("date_from"):
            docs = docs.filter(created_at__date__gte=request.query_params["date_from"])
        if request.query_params.get("date_to"):
            docs = docs.filter(created_at__date__lte=request.query_params["date_to"])
        allowed = borner(documents_visibles_par(request.user, docs.select_related("dossier", "uploaded_by", "validated_by").defer("extracted_text")), request)
        log_event(request, "document_searched", "search", q)
        return Response(DocumentSerializer(allowed, many=True, context=contexte_liste(request, allowed)).data)


class SavedSearchView(APIView):
    def get(self, request):
        return Response([{"id": item.id, "name": item.name, "filters": item.filters, "createdAt": item.created_at.isoformat(), "updatedAt": item.updated_at.isoformat()} for item in SavedSearch.objects.filter(user=request.user).order_by("name")])

    def post(self, request):
        name = str(request.data.get("name", "")).strip()
        filters = request.data.get("filters", {})
        if not name or not isinstance(filters, dict):
            return Response({"detail": "Un nom et des filtres valides sont requis."}, status=400)
        allowed = {"q", "type", "dossier", "client", "partie", "reference", "statut", "niveau", "domaine", "responsable", "date_from", "date_to"}
        filters = {str(k): str(v)[:200] for k, v in filters.items() if k in allowed and str(v).strip()}
        item, created = SavedSearch.objects.update_or_create(user=request.user, name=name[:120], defaults={"filters": filters})
        log_event(request, "saved_search_created" if created else "saved_search_updated", "saved_search", str(item.pk))
        return Response({"id": item.id, "name": item.name, "filters": item.filters}, status=201 if created else 200)

    def delete(self, request):
        item = SavedSearch.objects.filter(user=request.user, pk=request.data.get("id")).first()
        if not item:
            return Response({"detail": "Recherche enregistrée introuvable."}, status=404)
        item_id = item.pk
        item.delete()
        log_event(request, "saved_search_deleted", "saved_search", str(item_id))
        return Response({"ok": True})


class QueueView(APIView):
    def get(self, request, nom=None):
        if request.user.role not in {"admin", "clerc"}: return Response({"detail": "Accès refusé."}, status=403)
        # La file de numérisation liste ce qui ATTEND un traitement humain :
        # les pièces fraîchement déposées (« à_indexer ») et celles renvoyées
        # par un contrôle qualité négatif (« à_corriger »). Les pièces déjà
        # contrôlées passent en « en_validation » : elles relèvent de la file
        # du notaire, comptée à part (pendingValidationCount).
        docs = file_de_numerisation(request.user).select_related("dossier", "uploaded_by", "validated_by").defer("extracted_text").order_by("-created_at")
        if nom:
            docs = docs.filter(nom__icontains=nom)
        docs = borner(docs, request)
        return Response(DocumentSerializer(docs, many=True, context=contexte_liste(request, docs)).data)
