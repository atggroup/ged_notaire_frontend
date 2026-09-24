import re
import unicodedata
import csv
import io
import json
import os
import zipfile
from datetime import date
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, serializers as drf_serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from ged_backend.confidentialite import est_plus_faible
from ged_backend.referentiels import DOMAINE_LABELS
from accounts.services import reevaluer_exigence_mfa
from permissions_app.access import documents_visibles_par, dossiers_visibles_par, has_dossier_access, has_document_access
from notifications.services import notify
from .checklist import completude, item_payload
from .models import Client, Dossier, DossierAssignment, DossierChecklistItem, DossierParty, PhysicalArchiveRecord

# Ordre du workflow des dossiers (mêmes étapes que l'écran « Avancement »).
WORKFLOW = [Dossier.Status.OPEN, Dossier.Status.IN_PROGRESS, Dossier.Status.WAITING, Dossier.Status.READY,
            Dossier.Status.FINALIZED, Dossier.Status.CLOSED, Dossier.Status.ARCHIVED]
# Étapes qui engagent l'étude : réservées au notaire.
ETAPES_NOTAIRE = {Dossier.Status.FINALIZED, Dossier.Status.CLOSED, Dossier.Status.ARCHIVED}


def transition_refusee(user, actuel: str, suivant: str) -> str | None:
    """Règle de transition côté serveur ; renvoie le motif de refus éventuel.

    Le notaire peut placer le dossier à n'importe quelle étape (réouverture
    comprise). Le clerc fait avancer d'une étape à la fois, ou revient en
    arrière, mais ne finalise, ne clôt ni n'archive."""
    if user.role == "admin" or suivant == actuel:
        return None
    if suivant in ETAPES_NOTAIRE:
        return "Finaliser, clore ou archiver un dossier relève du notaire."
    i, j = WORKFLOW.index(actuel), WORKFLOW.index(suivant)
    if j > i + 1:
        return "Le dossier avance d'une étape à la fois."
    return None


def client_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "-", normalized).strip("-")[:96]


def next_dossier_reference(domaine: str) -> str:
    """Cadrage §3 : identifiant maître de dossier DOM-AAAA-NNNNN.

    La séquence NNNNN (5 chiffres) est propre à chaque couple domaine/année,
    conformément au référentiel des domaines (§4).

    La valeur vient d'un compteur verrouillé : `count() + 1` attribuait la même
    référence à deux créations simultanées (erreur 500 sur la contrainte
    d'unicité). Le compteur reprend l'existant à sa première utilisation.
    """
    from core.coordination import next_sequence
    year = timezone.localdate().year
    base = f"{domaine}-{year}"

    def existant():
        numeros = [int(ref.rsplit("-", 1)[-1]) for ref in Dossier.objects.filter(reference__startswith=base + "-").values_list("reference", flat=True)
                   if ref.rsplit("-", 1)[-1].isdigit()]
        return max(numeros, default=0)

    while True:
        reference = f"{base}-{next_sequence(f'dossier:{base}', floor=existant):05d}"
        # Filet : une référence posée à la main (import, reprise) est sautée.
        if not Dossier.objects.filter(reference=reference).exists():
            return reference


def next_client_reference(nom: str) -> str:
    from core.coordination import next_sequence
    while True:
        reference = f"CLI-{client_key(nom)[:20]}-{next_sequence('client', floor=Client.objects.count):05d}"
        if not Client.objects.filter(reference=reference).exists():
            return reference


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


class ClientDirectoryView(APIView):
    """Annuaire métier des clients, distinct des dossiers.

    Un même client peut participer à plusieurs dossiers. Les collaborateurs
    ne voient que les clients rattachés à des dossiers auxquels ils ont accès.
    """
    def get(self, request):
        if request.user.role not in {"admin", "clerc", "collaborateur"}:
            return Response({"detail": "Accès refusé."}, status=403)
        dossiers = list(dossiers_visibles_par(request.user, Dossier.objects.prefetch_related("parties__client")))
        dossier_map = {}
        client_ids = set()
        for dossier in dossiers:
            for party in dossier.parties.all():
                client_ids.add(party.client_id)
                dossier_map.setdefault(party.client_id, []).append({
                    "reference": dossier.reference,
                    "nom": dossier.nom,
                    "role": party.role,
                })
        clients = Client.objects.filter(pk__in=client_ids).order_by("nom")
        return Response([{
            "id": client.pk,
            "reference": client.reference,
            "nom": client.nom,
            "kind": client.kind,
            "email": client.email,
            "telephone": client.telephone,
            "dossiersCount": len(dossier_map.get(client.pk, [])),
            "dossiers": dossier_map.get(client.pk, []),
        } for client in clients])

    def post(self, request):
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Création de client réservée au notaire ou au clerc principal."}, status=403)
        nom = str(request.data.get("nom", "")).strip()
        if not nom:
            return Response({"nom": ["Le nom du client est obligatoire."]}, status=400)
        kind = request.data.get("kind", Client.Kind.PERSON)
        if kind not in Client.Kind.values:
            return Response({"kind": ["Type de client invalide."]}, status=400)
        client = Client.objects.filter(nom__iexact=nom).first()
        if client:
            return Response({"detail": "Un client portant ce nom existe déjà.", "reference": client.reference}, status=409)
        reference = next_client_reference(nom)
        client = Client.objects.create(
            reference=reference,
            nom=nom,
            kind=kind,
            email=str(request.data.get("email", "")).strip(),
            telephone=str(request.data.get("telephone", "")).strip(),
        )
        log_event(request, "client_created", "client", client.reference)
        return Response({
            "id": client.pk, "reference": client.reference, "nom": client.nom,
            "kind": client.kind, "email": client.email, "telephone": client.telephone,
            "dossiersCount": 0, "dossiers": [],
        }, status=201)


class DossierView(APIView):
    def get(self, request):
        if request.user.role not in {"admin", "clerc", "collaborateur"}:
            return Response({"detail": "Accès refusé."}, status=403)
        # Nombre de pièces calculé par la base (sous-requête) : la liste
        # préchargeait auparavant TOUTES les pièces de tous les dossiers pour
        # les compter. Bornée, avec `limit`/`offset` et le total en en-tête.
        from django.db.models import Count, IntegerField, OuterRef, Subquery
        from django.db.models.functions import Coalesce
        from documents.models import Document
        compte = (Document.objects.filter(dossier=OuterRef("pk")).order_by().values("dossier")
                  .annotate(n=Count("pk")).values("n"))
        visibles = dossiers_visibles_par(request.user, Dossier.objects.all())
        total = visibles.count()
        try:
            limite = min(int(request.query_params.get("limit", 1000)), 1000)
            depart = max(int(request.query_params.get("offset", 0)), 0)
        except (TypeError, ValueError):
            limite, depart = 1000, 0
        items = list(Dossier.objects.filter(pk__in=visibles.values("pk"))
                     .annotate(nb_pieces=Coalesce(Subquery(compte, output_field=IntegerField()), 0))
                     .prefetch_related("parties__client", "assignments__user")
                     .order_by("-created_at")[depart:depart + limite])
        reponse = Response([{
            "reference": item.reference, "nom": item.nom, "client": item.client,
            "objet": item.objet, "statut": item.statut,
            "domaine": item.domaine, "domaineLabel": DOMAINE_LABELS.get(item.domaine, item.domaine),
            "niveauDeConfidentialite": item.niveau_de_confidentialite,
            "legalHold": item.legal_hold,
            "parties": [{"nom": party.client.nom, "role": party.role, "relationship": party.relationship, "isPrimary": party.is_primary} for party in item.parties.all()],
            "assignments": [{"userId": a.user_id, "name": a.user.display_name, "role": a.role, "dueAt": a.due_at.isoformat() if a.due_at else None} for a in item.assignments.all()],
            "documentsCount": item.nb_pieces, "createdAt": item.created_at.isoformat(),
        } for item in items])
        reponse["X-Total-Count"] = str(total)
        return reponse

    def post(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Création de dossier réservée au notaire."}, status=403)
        nom = request.data.get("nom") or request.data.get("nom_dossier") or request.data.get("objet")
        if not nom:
            return Response({"nom": ["Ce champ est obligatoire."]}, status=400)
        client = str(request.data.get("client", "")).strip()
        if not client:
            return Response({"client": ["Le client principal est obligatoire pour créer une affaire."]}, status=400)
        domaine = request.data.get("domaine")
        if domaine not in DOMAINE_LABELS:
            return Response({"domaine": ["Domaine invalide. Choisissez un code du référentiel (§4)."]}, status=400)
        confidentiality = request.data.get("niveau_de_confidentialite", request.data.get("niveau", Dossier.Confidentiality.RESTRICTED))
        if confidentiality not in Dossier.Confidentiality.values:
            return Response({"niveau_de_confidentialite": ["Valeur invalide."]}, status=400)
        # Le statut initial était recopié tel quel depuis la requête : un
        # dossier pouvait naître « archivé » ou avec une valeur hors référentiel.
        statut_initial = request.data.get("statut") or Dossier.Status.OPEN
        if statut_initial not in {Dossier.Status.OPEN, Dossier.Status.IN_PROGRESS, Dossier.Status.WAITING}:
            return Response({"statut": ["Un dossier s'ouvre « ouvert », « en instruction » ou « en attente de pièces »."]}, status=400)
        from .checklist import generer_checklist
        with transaction.atomic():
            reference = next_dossier_reference(domaine)
            dossier = Dossier.objects.create(reference=reference, domaine=domaine, nom=nom, client=client, client_key=client_key(client), objet=request.data.get("objet", ""), statut=statut_initial, niveau_de_confidentialite=confidentiality, created_by=request.user, status_changed_at=timezone.now())
            client_record = Client.objects.filter(nom__iexact=client).first()
            if not client_record:
                client_record = Client.objects.create(reference=next_client_reference(client), nom=client, kind=request.data.get("client_kind", Client.Kind.PERSON))
            DossierParty.objects.create(dossier=dossier, client=client_record, role="client_principal", is_primary=True)
            for party in request.data.get("parties", []):
                party_name = str(party.get("nom", "")).strip()
                if not party_name:
                    continue
                record = Client.objects.filter(nom=party_name).first() or Client.objects.create(
                    nom=party_name, reference=next_client_reference(party_name), kind=party.get("kind", Client.Kind.PERSON))
                DossierParty.objects.get_or_create(dossier=dossier, client=record, role=str(party.get("role", "partie"))[:80], defaults={"relationship": str(party.get("relationship", ""))[:120]})
            # Checklist générée depuis les modèles du domaine.
            items = generer_checklist(dossier)
        log_event(request, "dossier_created", "dossier", str(dossier.pk), metadata={"reference": dossier.reference, "checklist_generee": len(items)})
        return Response({"reference": dossier.reference, "nom": dossier.nom, "client": dossier.client, "domaine": dossier.domaine, "statut": dossier.statut, "niveauDeConfidentialite": dossier.niveau_de_confidentialite, "checklistItems": len(items)}, status=status.HTTP_201_CREATED)

    # NOTE: la logique de mise à jour (niveau de confidentialité, legalHold,
    # statut) vit désormais dans DossierDetailView.patch, sur la route
    # /api/dossiers/<reference> — c'est la route que le frontend appelle
    # réellement (voir operations.js). L'ancienne méthode patch() ici était
    # branchée sur /api/dossiers (liste), jamais appelée par le frontend,
    # ce qui causait le 405.


def dossier_or_404(request, reference):
    item = Dossier.objects.prefetch_related("documents", "parties__client", "assignments__user", "checklist_items", "physical_records").filter(reference=reference).first()
    return item if item and has_dossier_access(request.user, item) else None


def dossier_payload(item: Dossier, request=None) -> dict:
    documents = []
    tasks = []
    if request is not None and getattr(request, "user", None) is not None:
        # Sans ce filtrage, l'onglet "Documents" fuiterait les pièces "Très
        # confidentiel" du dossier à quiconque a simplement accès au dossier
        # (voir permissions_app.access.has_document_access : un document
        # sensible exige une permission explicite, distincte de l'accès au
        # dossier parent).
        from documents.serializers import contexte_liste, DocumentSerializer
        visible_docs = list(documents_visibles_par(request.user, item.documents.filter(is_archived=False).select_related("dossier", "uploaded_by", "validated_by").defer("extracted_text")))
        documents = DocumentSerializer(visible_docs, many=True, context=contexte_liste(request, visible_docs)).data
        # Les tâches sont personnelles : un collaborateur ne voit que les
        # siennes sur ce dossier, tandis qu'admin/clerc (qui ont déjà accès
        # au dossier pour arriver jusqu'ici) voient l'ensemble des tâches liées.
        dossier_tasks = item.tasks.select_related("assigned_to", "assigned_by")
        if request.user.role not in {"admin", "clerc"}:
            dossier_tasks = dossier_tasks.filter(assigned_to=request.user)
        from notifications.views import task_payload
        tasks = [task_payload(t) for t in dossier_tasks.order_by("due_at", "-created_at")]
    return {
        "reference": item.reference, "nom": item.nom, "client": item.client, "objet": item.objet,
        "statut": item.statut, "domaine": item.domaine, "niveauDeConfidentialite": item.niveau_de_confidentialite,
        "legalHold": item.legal_hold, "legalHoldReason": item.legal_hold_reason,
        "parties": [{"id": p.client_id, "reference": p.client.reference, "nom": p.client.nom, "role": p.role, "relationship": p.relationship, "isPrimary": p.is_primary} for p in item.parties.all()],
        "assignments": [{"id": a.id, "userId": a.user_id, "name": a.user.display_name, "role": a.role, "assignedAt": a.assigned_at.isoformat(), "dueAt": a.due_at.isoformat() if a.due_at else None} for a in item.assignments.all()],
        "checklist": [item_payload(c) for c in sorted(item.checklist_items.all(), key=lambda c: (c.created_at, c.pk))],
        "completude": completude(item),
        "statusChangedAt": item.status_changed_at.isoformat() if item.status_changed_at else None,
        "physicalRecords": [physical_payload(p) for p in item.physical_records.all()],
        "documents": documents,
        "tasks": tasks,
    }


def physical_payload(item: PhysicalArchiveRecord) -> dict:
    return {"id": item.id, "document": item.document.reference if item.document_id else None, "room": item.room, "cabinet": item.cabinet, "shelf": item.shelf, "box": item.box, "folder": item.folder, "checkedOutBy": item.checked_out_by.display_name if item.checked_out_by_id else None, "checkedOutAt": item.checked_out_at.isoformat() if item.checked_out_at else None, "checkoutReason": item.checkout_reason, "returnedAt": item.returned_at.isoformat() if item.returned_at else None}


class DossierDetailView(APIView):
    def get(self, request, reference):
        item = dossier_or_404(request, reference)
        if not item:
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        log_event(request, "dossier_viewed", "dossier", item.reference)
        return Response(dossier_payload(item, request))

    def patch(self, request, reference):
        dossier = Dossier.objects.filter(reference=reference).first()
        if not dossier or not has_dossier_access(request.user, dossier):
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Vous ne pouvez pas faire avancer le workflow de ce dossier."}, status=403)
        level = request.data.get("niveau_de_confidentialite", request.data.get("niveau"))
        changed = []
        a_relever, a_abaisser, propagation = [], [], None
        if level is not None:
            if request.user.role != "admin":
                return Response({"detail": "Seul le notaire peut modifier la confidentialité d'un dossier."}, status=403)
            if level not in Dossier.Confidentiality.values:
                return Response({"niveau_de_confidentialite": ["Valeur invalide."]}, status=400)
            ancien_niveau = dossier.niveau_de_confidentialite
            dossier.niveau_de_confidentialite = level
            changed.append("niveau_de_confidentialite")
            # Le niveau du dossier est un PLANCHER, pas une valeur qu'on recopie.
            # Relever le dossier relève les pièces restées en dessous ; abaisser
            # le dossier ne touche à rien par défaut, sans quoi une exception
            # posée pièce par pièce par le notaire — un testament classé « Très
            # confidentiel » dans un dossier qu'on rouvre — était déclassifiée
            # en silence et redevenait lisible par tout affecté au dossier.
            # Les pièces ne sont écrites qu'une fois TOUTES les validations
            # passées (plus bas) : un refus ne doit rien laisser derrière lui.
            pieces = list(dossier.documents.filter(is_archived=False))
            a_relever = [p for p in pieces if est_plus_faible(p.niveau_de_confidentialite, level)]
            a_abaisser = []
            if str(request.data.get("appliquerAuxPieces", "")).lower() in {"1", "true", "oui"}:
                # Abaissement explicitement demandé par le notaire, après
                # confirmation à l'écran : on le trace tel quel.
                a_abaisser = [p for p in pieces if est_plus_faible(level, p.niveau_de_confidentialite)]
            propagation = {
                "niveau_precedent": ancien_niveau,
                "niveau_suivant": level,
                "pieces_relevees": len(a_relever),
                "pieces_abaissees": len(a_abaisser),
            }
        if "legalHold" in request.data or "legal_hold" in request.data:
            if request.user.role != "admin":
                return Response({"detail": "Seul le notaire peut modifier la conservation légale."}, status=403)
            dossier.legal_hold = bool(request.data.get("legalHold", request.data.get("legal_hold")))
            dossier.legal_hold_reason = str(request.data.get("legalHoldReason", request.data.get("legal_hold_reason", ""))) if dossier.legal_hold else ""
            if dossier.legal_hold and not dossier.legal_hold_reason:
                return Response({"legalHoldReason": ["Un motif est obligatoire pour geler un dossier."]}, status=400)
            changed.extend(["legal_hold", "legal_hold_reason"])
        if "statut" in request.data:
            next_status = request.data["statut"]
            if next_status not in Dossier.Status.values:
                return Response({"statut": ["Statut de dossier invalide."]}, status=400)
            if dossier.legal_hold and next_status == Dossier.Status.ARCHIVED:
                return Response({"statut": ["Un dossier gelé ne peut pas être archivé."]}, status=409)
            motif = transition_refusee(request.user, dossier.statut, next_status)
            if motif:
                return Response({"statut": [motif]}, status=403)
            ancien_statut = dossier.statut
            if next_status != ancien_statut:
                dossier.statut = next_status
                dossier.status_changed_at = timezone.now()
                changed.extend(["statut", "status_changed_at"])
            else:
                changed.append("statut")
        if not changed:
            return Response({"detail": "Aucune modification demandée."}, status=400)
        with transaction.atomic():
            dossier.save(update_fields=changed)
            for piece in [*a_relever, *a_abaisser]:
                piece.niveau_de_confidentialite = level
                piece.save(update_fields=["niveau_de_confidentialite", "updated_at"])
        metadata = {"fields": changed}
        if propagation:
            metadata["propagation"] = propagation
        avertissements = []
        if "status_changed_at" in changed:
            etat = completude(dossier)
            metadata["statut"] = {"precedent": ancien_statut, "suivant": dossier.statut, "pieces_manquantes": etat["manquants"]}
            if dossier.statut in {Dossier.Status.READY, Dossier.Status.FINALIZED} and etat["manquants"]:
                avertissements.append(f"{etat['manquants']} pièce(s) obligatoire(s) de la checklist ne sont pas encore complètes.")
            _notifier_changement_statut(request, dossier, ancien_statut)
        action = "dossier_legal_hold_updated" if "legal_hold" in changed else (
            "dossier_status_updated" if changed == ["statut", "status_changed_at"] else "dossier_confidentiality_updated")
        log_event(request, action, "dossier", str(dossier.pk), metadata=metadata)
        return Response({
            "warnings": avertissements, "statut": dossier.statut,
            "ok": True, "reference": dossier.reference,
            "niveauDeConfidentialite": dossier.niveau_de_confidentialite,
            "legalHold": dossier.legal_hold,
            "piecesRelevees": len(a_relever), "piecesAbaissees": len(a_abaisser),
        })


STATUT_LABELS = dict(Dossier.Status.choices)


def _notifier_changement_statut(request, dossier, ancien: str) -> None:
    """Les personnes affectées au dossier suivent son avancement."""
    from notifications.services import emit
    destinataires = [a.user for a in dossier.assignments.select_related("user") if a.user_id != request.user.pk]
    emit(destinataires, "dossier_status", "Avancement du dossier",
         f"Le dossier {dossier.reference} passe de « {STATUT_LABELS.get(ancien, ancien)} » à "
         f"« {STATUT_LABELS.get(dossier.statut, dossier.statut)} » ({request.user.display_name}).",
         target_type="dossier", target_id=dossier.reference)


class DossierAssignmentView(APIView):
    def post(self, request, reference):
        if request.user.role != "admin":
            return Response({"detail": "Affectation réservée au notaire."}, status=403)
        item = Dossier.objects.filter(reference=reference).first()
        from accounts.models import User
        user = User.objects.filter(pk=request.data.get("userId") or request.data.get("user")).first()
        role = request.data.get("role")
        if not item or not user or role not in DossierAssignment.Role.values:
            return Response({"detail": "Dossier, utilisateur et rôle d'affectation valides requis."}, status=400)
        due_at = parse_datetime(request.data.get("dueAt", "")) if request.data.get("dueAt") else None
        if due_at and timezone.is_naive(due_at): due_at = timezone.make_aware(due_at)
        assignment, created = DossierAssignment.objects.update_or_create(dossier=item, user=user, role=role, defaults={"assigned_by": request.user, "due_at": due_at})
        log_event(request, "dossier_assignment_created" if created else "dossier_assignment_updated", "dossier", item.reference, metadata={"user": user.pk, "role": role})
        # Une affectation ouvre le dossier : même exigence de second facteur
        # que pour une habilitation nominative.
        reevaluer_exigence_mfa(user, item, request=request)
        if created:
            # Sans ceci, l'utilisateur affecté n'est jamais alerté : il ne
            # découvre le dossier que s'il pense à aller le chercher.
            notify(
                user,
                "dossier_assignment",
                "Nouveau dossier affecté",
                f"{request.user.display_name} vous a affecté le dossier « {item.nom} » ({item.reference}).",
            )
        return Response({"id": assignment.id, "created": created}, status=201 if created else 200)

    def delete(self, request, reference):
        if request.user.role != "admin":
            return Response({"detail": "Affectation réservée au notaire."}, status=403)
        count, _ = DossierAssignment.objects.filter(dossier__reference=reference, pk=request.data.get("id")).delete()
        if not count:
            return Response({"detail": "Affectation introuvable."}, status=404)
        log_event(request, "dossier_assignment_removed", "dossier", reference)
        return Response({"ok": True})


class DossierChecklistView(APIView):
    def post(self, request, reference):
        item = dossier_or_404(request, reference)
        if not item or request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        label = str(request.data.get("label", "")).strip()
        if not label:
            return Response({"label": ["Libellé obligatoire."]}, status=400)
        from ged_backend.referentiels import TYPE_DOCUMENT_LABELS
        type_code = str(request.data.get("typeCode") or request.data.get("type_code") or "").strip()
        if type_code and type_code not in TYPE_DOCUMENT_LABELS:
            return Response({"typeCode": ["Code type invalide (référentiel §5)."]}, status=400)
        requis = request.data.get("required", True)
        requis = str(requis).lower() not in {"0", "false", "non"} if not isinstance(requis, bool) else requis
        check = DossierChecklistItem.objects.create(dossier=item, label=label[:255], required=requis, type_code=type_code)
        # Une pièce du bon type déjà déposée répond aussitôt à l'élément.
        if type_code:
            from documents.models import Document
            deja = Document.objects.filter(dossier=item, type_code=type_code, is_current=True, trashed_at__isnull=True).exclude(
                statut=Document.Status.DESTROYED).exclude(checklist_items__isnull=False).order_by("-created_at").first()
            if deja:
                check.document, check.received_at = deja, timezone.now()
                check.save(update_fields=["document", "received_at"])
        log_event(request, "dossier_checklist_item_created", "dossier", item.reference, metadata={"item": check.id, "type_code": type_code})
        return Response({"id": check.id, **item_payload(check)}, status=201)

    def patch(self, request, reference):
        item = dossier_or_404(request, reference)
        check = DossierChecklistItem.objects.filter(dossier=item, pk=request.data.get("id")).first() if item else None
        if not check:
            return Response({"detail": "Élément de checklist introuvable."}, status=404)
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Seuls le notaire et le clerc tiennent la checklist."}, status=403)
        etait_complet = completude(item)["complet"]
        completed = bool(request.data.get("completed"))
        check.completed_at = timezone.now() if completed else None
        check.completed_by = request.user if completed else None
        check.save(update_fields=["completed_at", "completed_by"])
        if not completed:
            # Une pièce rouverte doit pouvoir être relancée de nouveau.
            from core.coordination import forget
            forget(f"checklist:{check.pk}:relance")
        log_event(request, "dossier_checklist_item_completed" if completed else "dossier_checklist_item_reopened", "dossier", item.reference, metadata={"item": check.id})
        etat = completude(item)
        if etat["complet"] and not etait_complet:
            from notifications.services import active_admins, emit
            destinataires = active_admins(exclude=request.user) + [
                a.user for a in item.assignments.select_related("user").filter(role=DossierAssignment.Role.CLERK) if a.user_id != request.user.pk]
            emit(destinataires, "dossier_complete", "Dossier complet",
                 f"Toutes les pièces obligatoires du dossier {item.reference} sont réunies. "
                 "Il peut passer à l'étape « Prêt pour acte » après vérification.",
                 target_type="dossier", target_id=item.reference)
            log_event(request, "dossier_checklist_complete", "dossier", item.reference, metadata=etat)
        return Response({"ok": True, "completed": completed, "completude": etat})


class PhysicalArchiveView(APIView):
    def post(self, request, reference, action="create"):
        item = dossier_or_404(request, reference)
        if not item or request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        if action == "create":
            fields = {field: str(request.data.get(field, "")).strip()[:80] for field in ("room", "cabinet", "shelf", "box", "folder")}
            if not all(fields.values()):
                return Response({"detail": "Salle, armoire, rayon, boîte et chemise sont obligatoires."}, status=400)
            from documents.models import Document
            doc = Document.objects.filter(reference=request.data.get("document"), dossier=item).first() if request.data.get("document") else None
            record = PhysicalArchiveRecord.objects.create(dossier=item, document=doc, **fields)
            log_event(request, "physical_archive_recorded", "dossier", item.reference, metadata={"record": record.pk})
            return Response(physical_payload(record), status=201)
        record = PhysicalArchiveRecord.objects.filter(dossier=item, pk=request.data.get("id")).select_related("checked_out_by", "document").first()
        if not record:
            return Response({"detail": "Original physique introuvable."}, status=404)
        if action == "checkout":
            reason = str(request.data.get("reason", "")).strip()
            if record.checked_out_at and not record.returned_at:
                return Response({"detail": "Cet original est déjà sorti."}, status=409)
            if not reason:
                return Response({"reason": ["Motif de sortie obligatoire."]}, status=400)
            record.checked_out_by, record.checked_out_at, record.checkout_reason, record.returned_at = request.user, timezone.now(), reason, None
            event = "physical_archive_checked_out"
        elif action == "return":
            if not record.checked_out_at or record.returned_at:
                return Response({"detail": "Cet original n'est pas actuellement sorti."}, status=409)
            record.returned_at = timezone.now()
            event = "physical_archive_returned"
        else:
            return Response({"detail": "Action physique invalide."}, status=404)
        record.save()
        log_event(request, event, "dossier", item.reference, metadata={"record": record.pk})
        return Response(physical_payload(record))


class DossierExportView(APIView):
    """Export d'un dossier — borné à ce que le demandeur a le droit de voir.

    L'accès au dossier n'emporte PAS l'accès à chacune de ses pièces : une
    pièce « Très confidentiel » exige une habilitation nominative (voir
    `permissions_app.access.has_document_access`). Sans le filtrage ci-dessous,
    l'export livrait en clair, à un simple affecté, les pièces que la route de
    détail lui refuse — c'est-à-dire toute la politique de confidentialité
    contournée par une porte de service, et d'un seul clic depuis l'écran.

    L'export reste sincère : il indique combien de pièces ont été écartées,
    sans jamais nommer celles que le demandeur n'a pas le droit de connaître.
    """

    def get(self, request, reference):
        item = dossier_or_404(request, reference)
        if not item:
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        from documents.models import Document
        from audit.models import AuditLog
        output = io.BytesIO()
        total = Document.objects.filter(dossier=item).count()
        docs = list(
            documents_visibles_par(
                request.user,
                Document.objects.filter(dossier=item).select_related("dossier", "uploaded_by", "validated_by"),
            ).order_by("created_at")
        )
        # Une pièce détruite n'a plus de binaire : la lire levait une
        # ValueError et renvoyait une 500 au lieu d'un export.
        avec_binaire = [doc for doc in docs if doc.fichier and doc.statut != Document.Status.DESTROYED]
        omises = total - len(docs)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadonnees.json", json.dumps(dossier_payload(item, request), ensure_ascii=False, indent=2))
            bordereau = io.StringIO()
            writer = csv.writer(bordereau)
            writer.writerow(["Référence", "Référence métier", "Nom", "Version", "SHA-256", "Statut", "Confidentialité", "Binaire joint"])
            for doc in docs:
                joint = doc in avec_binaire
                writer.writerow([doc.reference, doc.code_notarial_actuel(), doc.nom, doc.version, doc.sha256, doc.statut, doc.niveau_de_confidentialite, "oui" if joint else "non"])
                if joint:
                    safe_name = os.path.basename(doc.original_filename).replace("\\", "_")
                    archive.writestr(f"Documents/{doc.reference}_{safe_name}", doc.decrypted_bytes())
            if omises:
                writer.writerow([])
                writer.writerow([f"{omises} pièce(s) du dossier ne figurent pas dans cet export : elles relèvent d'une habilitation que vous ne détenez pas."])
            archive.writestr("bordereau.csv", bordereau.getvalue())
            audit_rows = AuditLog.objects.filter(target_id__in=[item.reference, *[doc.reference for doc in docs]]).order_by("timestamp")
            archive.writestr("historique.json", json.dumps([{"date": row.timestamp.isoformat(), "action": row.action, "cible": row.target_id, "résultat": row.result, "empreinte": row.entry_hash} for row in audit_rows], ensure_ascii=False, indent=2))
        log_event(request, "dossier_exported", "dossier", item.reference, metadata={"pieces_exportees": len(avec_binaire), "pieces_omises": omises})
        response = HttpResponse(output.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{item.reference}.zip"'
        return response


def template_payload(t) -> dict:
    return {"id": t.id, "domaine": t.domaine, "label": t.label, "typeCode": t.type_code, "required": t.required,
            "reminderDays": t.reminder_days, "order": t.order, "active": t.active}


class ChecklistTemplateView(APIView):
    """Modèles de checklist par domaine. Lecture : notaire et clerc ;
    modification : notaire seul (ce sont ses exigences)."""

    def get(self, request):
        if request.user.role not in {"admin", "clerc"}:
            return Response({"detail": "Accès refusé."}, status=403)
        from .models import ChecklistTemplate
        items = ChecklistTemplate.objects.all()
        if request.query_params.get("domaine"):
            items = items.filter(domaine=request.query_params["domaine"])
        return Response([template_payload(t) for t in items])

    def _valider(self, request, instance=None):
        from ged_backend.referentiels import TYPE_DOCUMENT_LABELS
        data = {}
        if instance is None or "domaine" in request.data:
            if request.data.get("domaine") not in DOMAINE_LABELS:
                return None, Response({"domaine": ["Domaine invalide."]}, status=400)
            data["domaine"] = request.data["domaine"]
        if instance is None or "label" in request.data:
            label = str(request.data.get("label", "")).strip()
            if not label:
                return None, Response({"label": ["Libellé obligatoire."]}, status=400)
            data["label"] = label[:255]
        if "typeCode" in request.data:
            code = str(request.data.get("typeCode") or "")
            if code and code not in TYPE_DOCUMENT_LABELS:
                return None, Response({"typeCode": ["Code type invalide."]}, status=400)
            data["type_code"] = code
        for champ, cle in (("required", "required"), ("active", "active")):
            if cle in request.data:
                data[champ] = bool(request.data[cle])
        for champ, cle in (("reminder_days", "reminderDays"), ("order", "order")):
            if cle in request.data:
                valeur = request.data[cle]
                if valeur in (None, ""):
                    data[champ] = None if champ == "reminder_days" else 0
                else:
                    try:
                        data[champ] = max(0, int(valeur))
                    except (TypeError, ValueError):
                        return None, Response({cle: ["Nombre entier attendu."]}, status=400)
        return data, None

    def post(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Les modèles de checklist relèvent du notaire."}, status=403)
        from .models import ChecklistTemplate
        data, erreur = self._valider(request)
        if erreur:
            return erreur
        if ChecklistTemplate.objects.filter(domaine=data["domaine"], label=data["label"]).exists():
            return Response({"detail": "Ce modèle existe déjà pour ce domaine."}, status=409)
        modele = ChecklistTemplate.objects.create(**data)
        log_event(request, "checklist_template_created", "checklist_template", str(modele.pk), metadata=template_payload(modele))
        return Response(template_payload(modele), status=201)

    def patch(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Les modèles de checklist relèvent du notaire."}, status=403)
        from .models import ChecklistTemplate
        modele = ChecklistTemplate.objects.filter(pk=request.data.get("id")).first()
        if not modele:
            return Response({"detail": "Modèle introuvable."}, status=404)
        avant = template_payload(modele)
        data, erreur = self._valider(request, modele)
        if erreur:
            return erreur
        for champ, valeur in data.items():
            setattr(modele, champ, valeur)
        modele.save()
        log_event(request, "checklist_template_updated", "checklist_template", str(modele.pk),
                  metadata={"previous": avant, "next": template_payload(modele)})
        return Response(template_payload(modele))


class ChecklistTemplateProposesView(APIView):
    """POST /api/checklist-templates/proposes — charge les modèles proposés
    (dossiers/modeles_checklist.json) sans écraser ceux déjà ajustés par le
    notaire. Même effet que `manage.py charger_modeles_checklist`, sans
    ligne de commande."""

    def post(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Les modèles de checklist relèvent du notaire."}, status=403)
        import io as _io
        from django.core.management import call_command
        from .models import ChecklistTemplate
        avant = ChecklistTemplate.objects.count()
        call_command("charger_modeles_checklist", stdout=_io.StringIO(), stderr=_io.StringIO())
        ajoutes = ChecklistTemplate.objects.count() - avant
        log_event(request, "checklist_templates_loaded", "checklist_template", "proposes", metadata={"ajoutes": ajoutes})
        return Response({"ajoutes": ajoutes, "total": ChecklistTemplate.objects.count()}, status=201 if ajoutes else 200)
