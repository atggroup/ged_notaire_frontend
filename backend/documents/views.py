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
from audit.services import log_event
from ged_backend.api import ContractSerializer
from ged_backend.referentiels import DOMAINE_LABELS, NATURE_LABELS, ORIGINE_LABELS, QUALITE_PARTIE_LABELS, SUPPORT_LABELS, TYPE_DOCUMENT_LABELS
from dossiers.models import Dossier
from notifications.services import notify, notify_admins
from permissions_app.access import has_document_access
from .crypto import encrypt
from .models import Document, DocumentFavorite, SavedSearch
from .ocr import extract_text
from .serializers import DocumentSerializer


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


MAGIC_TYPES = [(b"%PDF-", "application/pdf"), (b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG\r\n\x1a\n", "image/png"), (b"II*\x00", "image/tiff"), (b"MM\x00*", "image/tiff")]


def actual_mime(data: bytes) -> str | None:
    return next((mime for signature, mime in MAGIC_TYPES if data.startswith(signature)), None)


def document_or_404(reference: str):
    return Document.objects.filter(reference=reference).select_related("dossier", "uploaded_by", "validated_by").first()


def next_document_codes(dossier: Dossier | None, type_code: str) -> tuple[str, int]:
    """Generate a non-semantic technical identity and business sequence."""
    sequence = Document.objects.filter(dossier=dossier).count() + 1
    return f"DOC_{uuid.uuid4().hex.upper()}", sequence


class DashboardSummaryView(APIView):
    def get(self, request):
        from accounts.models import User
        from audit.models import AuditLog
        from audit.services import security_alerts
        from dossiers.models import Dossier, DossierChecklistItem
        from notifications.models import Task
        from permissions_app.access import has_dossier_access
        from permissions_app.models import AccessRequest
        from .crypto import key_ring_status
        visible_docs = [doc for doc in Document.objects.filter(is_archived=False) if has_document_access(request.user, doc)]
        pending_validation = [doc for doc in visible_docs if doc.statut == Document.Status.PENDING]
        recent = sorted(visible_docs, key=lambda d: d.created_at, reverse=True)[:4]
        visible_dossier_ids = {doc.dossier_id for doc in visible_docs if doc.dossier_id}
        queue_count = Document.objects.filter(statut__in=[Document.Status.DRAFT, Document.Status.PENDING]).count()
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
        visible_dossiers = [d for d in Dossier.objects.filter(statut__in=open_statuses) if has_dossier_access(request.user, d)]
        dossiers_sensibles = [d for d in visible_dossiers if d.niveau_de_confidentialite in {Dossier.Confidentiality.CONFIDENTIAL, Dossier.Confidentiality.VERY_CONFIDENTIAL}]
        dossiers_bloques = [d for d in visible_dossiers if d.legal_hold]
        pieces_manquantes = DossierChecklistItem.objects.filter(dossier__in=[d.pk for d in visible_dossiers], required=True, completed_at__isnull=True).select_related("dossier")
        echeances = Task.objects.filter(assigned_to=request.user, status__in=[Task.Status.OPEN, Task.Status.IN_PROGRESS], due_at__isnull=False).order_by("due_at")[:10]

        summary = {
            "documentsArchivedCount": Document.objects.filter(is_archived=True).count(),
            "documentsAccessibleCount": len(visible_docs),
            "dossiersCount": len(visible_dossier_ids) if request.user.role != "admin" else Document.objects.filter(is_archived=False, dossier__isnull=False).values("dossier_id").distinct().count(),
            "activeUsersCount": User.objects.filter(is_active=True).count(),
            "pendingValidationCount": len(pending_validation),
            "pendingIndexationCount": queue_count,
            "pendingAccessRequestCount": access_requests,
            "consultationsCount": consultations,
            "pendingAccessRequests": [{"reference": item.document.reference if item.document_id else "—", "status": item.get_status_display(), "createdAt": item.created_at.isoformat(), "demandeur": item.requester.display_name if request.user.role == "admin" else None} for item in pending_requests[:3]],
            "recentDocuments": DocumentSerializer(recent, many=True, context={"request": request}).data,
            "dossiersSensibles": [{"reference": d.reference, "nom": d.nom, "niveau": d.niveau_de_confidentialite} for d in dossiers_sensibles[:10]],
            "dossiersSensiblesCount": len(dossiers_sensibles),
            "dossiersBloques": [{"reference": d.reference, "nom": d.nom, "motif": d.legal_hold_reason} for d in dossiers_bloques[:10]],
            "dossiersBloquesCount": len(dossiers_bloques),
            "piecesManquantes": [{"dossier": item.dossier.reference, "libelle": item.label} for item in pieces_manquantes[:10]],
            "piecesManquantesCount": pieces_manquantes.count(),
            "echeances": [{"id": t.id, "titre": t.title, "dossier": t.dossier.reference if t.dossier_id else None, "echeance": t.due_at.isoformat(), "priorite": t.priority} for t in echeances],
        }
        if request.user.role == "admin":
            summary["alertesSecurite"] = security_alerts()
            summary["chiffrement"] = key_ring_status()
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
        allowed = [doc for doc in docs if has_document_access(request.user, doc)]
        return Response(DocumentSerializer(allowed, many=True, context={"request": request}).data)

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
    mime = actual_mime(raw[:32])
    if mime is None:
        return Response({"fichier": ["Type réel non autorisé : PDF, JPEG, PNG ou TIFF uniquement."]}, status=400)
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
    try:
        with transaction.atomic():
            # The case policy is the default. A notaire may add a more
            # restrictive documentary exception, never a weaker one.
            level = requested_level if request.user.role == "admin" else dossier.niveau_de_confidentialite
            reference, sequence = next_document_codes(dossier, type_code or "DOC")
            encrypted, key_id = encrypt(raw)
            doc = Document(
                reference=reference, sequence=sequence, dossier=dossier, type=type_, type_code=type_code or "",
                origine=origine, support=support, nature=nature, qualite_partie=qualite_partie,
                nom=request.data.get("nom") or uploaded.name, original_filename=uploaded.name, content_type=mime,
                size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), encryption_key_id=key_id, niveau_de_confidentialite=level,
                master_reference=reference, statut=Document.Status.TO_INDEX,
                uploaded_by=request.user,
            )
            doc.fichier.save(uploaded.name, ContentFile(encrypted), save=False)
            doc.save()
            doc.code_notarial = doc.code_notarial_actuel()
            doc.save(update_fields=["code_notarial"])
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=503)
    process_ocr(doc)
    log_event(request, "document_uploaded", "document", doc.reference, metadata={"sha256": doc.sha256, "content_type": mime, "ocr_status": doc.ocr_status})
    notify_admins("document", "Document à contrôler", f"{request.user.display_name} a déposé « {doc.nom} » ({doc.reference}). Contrôle qualité requis.", exclude=request.user if request.user.role == "admin" else None)
    return Response(DocumentSerializer(doc, context={"request": request}).data, status=status.HTTP_201_CREATED)


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
        response["Content-Disposition"] = f'inline; filename="{doc.original_filename}"'
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
        response["Content-Disposition"] = f'attachment; filename="{doc.original_filename}"'
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
        doc.is_archived = True
        doc.statut = Document.Status.ARCHIVED
        if doc.dossier and doc.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : l'archivage ou la purge est bloqué."}, status=409)
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
        return Response(DocumentSerializer(versions, many=True, context={"request": request}).data)

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
        mime = actual_mime(raw[:32])
        if not mime:
            return Response({"fichier": ["Type réel non autorisé."]}, status=400)
        with transaction.atomic():
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
            )
            new_doc.fichier.save(uploaded.name, ContentFile(new_encrypted), save=False)
            new_doc.save()
            new_doc.code_notarial = new_doc.code_notarial_actuel()
            new_doc.save(update_fields=["code_notarial"])
        log_event(request, "document_new_version", "document", new_doc.reference, metadata={"previous": current.reference, "sha256": new_doc.sha256})
        return Response(DocumentSerializer(new_doc, context={"request": request}).data, status=status.HTTP_201_CREATED)


class FavoriteDocumentView(APIView):
    def post(self, request):
        doc = document_or_404(request.data.get("ref"))
        if not doc: return Response({"detail": "Document introuvable."}, status=404)
        favorite, created = DocumentFavorite.objects.get_or_create(user=request.user, document=doc)
        if not created: favorite.delete()
        return Response({"ok": True, "is_favorite": created})


class DocumentTrashView(APIView):
    """A recoverable deletion workflow.  Bytes are never destroyed here."""
    def post(self, request, reference, action):
        doc = document_or_404(reference)
        if not doc:
            return Response({"detail": "Document introuvable."}, status=404)
        if request.user.role != "admin":
            return Response({"detail": "La corbeille documentaire est réservée au notaire."}, status=403)
        if doc.dossier and doc.dossier.legal_hold:
            return Response({"detail": "Ce dossier est gelé : aucune destruction ou mise à la corbeille n'est autorisée."}, status=409)
        now = timezone.now()
        if action == "trash":
            if doc.trashed_at:
                return Response({"detail": "Document déjà dans la corbeille."}, status=409)
            doc.trashed_at, doc.trashed_by, doc.statut = now, request.user, Document.Status.TRASHED
            event = "document_trashed"
        elif action == "restore":
            if not doc.trashed_at:
                return Response({"detail": "Ce document n'est pas dans la corbeille."}, status=409)
            doc.trashed_at, doc.trashed_by = None, None
            doc.destruction_requested_at = doc.destruction_requested_by = None
            doc.destruction_authorized_at = doc.destruction_authorized_by = None
            doc.statut = Document.Status.ARCHIVED if doc.is_archived else Document.Status.TO_INDEX
            event = "document_restored"
        elif action == "request-destruction":
            if not doc.trashed_at:
                return Response({"detail": "Placez d'abord le document dans la corbeille."}, status=409)
            doc.destruction_requested_at, doc.destruction_requested_by = now, request.user
            doc.statut, event = Document.Status.DESTRUCTION_REQUESTED, "document_destruction_requested"
        elif action == "authorize-destruction":
            if not doc.destruction_requested_at:
                return Response({"detail": "Aucune demande de destruction n'est en attente."}, status=409)
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
        log_event(request, event, "document", doc.reference)
        return Response(DocumentSerializer(doc, context={"request": request}).data)


class SearchView(APIView):
    def get(self, request):
        q = request.query_params.get("q", "").strip()
        type_ = request.query_params.get("type", "")
        dossier = request.query_params.get("dossier", "").strip()
        docs = Document.objects.filter(is_archived=False, trashed_at__isnull=True)
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
        allowed = [doc for doc in docs.distinct() if has_document_access(request.user, doc)]
        log_event(request, "document_searched", "search", q)
        return Response(DocumentSerializer(allowed, many=True, context={"request": request}).data)


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
        docs = Document.objects.filter(statut__in=[Document.Status.DRAFT, Document.Status.PENDING]).select_related("uploaded_by").order_by("-created_at")
        if nom:
            docs = docs.filter(nom__icontains=nom)
        return Response(DocumentSerializer(docs, many=True, context={"request": request}).data)
