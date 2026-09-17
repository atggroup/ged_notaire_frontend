import re
import unicodedata
import csv
import io
import json
import os
import zipfile
from datetime import date
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status, serializers as drf_serializers
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework.views import APIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from ged_backend.referentiels import DOMAINE_LABELS
from permissions_app.access import has_dossier_access, has_document_access
from notifications.services import notify
from .models import Client, Dossier, DossierAssignment, DossierChecklistItem, DossierParty, PhysicalArchiveRecord


def client_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "-", normalized).strip("-")[:96]


def next_dossier_reference(domaine: str) -> str:
    """Cadrage §3 : identifiant maître de dossier DOM-AAAA-NNNNN.

    La séquence NNNNN (5 chiffres) est propre à chaque couple domaine/année,
    conformément au référentiel des domaines (§4).
    """
    year = date.today().year
    base = f"{domaine}-{year}"
    sequence = Dossier.objects.filter(reference__startswith=base + "-").count() + 1
    return f"{base}-{sequence:05d}"


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
        dossiers = [d for d in Dossier.objects.prefetch_related("parties__client").all() if has_dossier_access(request.user, d)]
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
        reference = f"CLI-{client_key(nom)[:20]}-{Client.objects.count() + 1:05d}"
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
        items = [item for item in Dossier.objects.prefetch_related("documents", "parties__client", "assignments__user").order_by("-created_at") if has_dossier_access(request.user, item)]
        return Response([{
            "reference": item.reference, "nom": item.nom, "client": item.client,
            "objet": item.objet, "statut": item.statut,
            "domaine": item.domaine, "domaineLabel": DOMAINE_LABELS.get(item.domaine, item.domaine),
            "niveauDeConfidentialite": item.niveau_de_confidentialite,
            "legalHold": item.legal_hold,
            "parties": [{"nom": party.client.nom, "role": party.role, "relationship": party.relationship, "isPrimary": party.is_primary} for party in item.parties.select_related("client")],
            "assignments": [{"userId": a.user_id, "name": a.user.display_name, "role": a.role, "dueAt": a.due_at.isoformat() if a.due_at else None} for a in item.assignments.all()],
            "documentsCount": item.documents.count(), "createdAt": item.created_at.isoformat(),
        } for item in items])

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
        reference = next_dossier_reference(domaine)
        dossier = Dossier.objects.create(reference=reference, domaine=domaine, nom=nom, client=client, client_key=client_key(client), objet=request.data.get("objet", ""), statut=request.data.get("statut", "ouvert"), niveau_de_confidentialite=confidentiality, created_by=request.user)
        client_record = Client.objects.filter(nom__iexact=client).first()
        if not client_record:
            client_record = Client.objects.create(reference=f"CLI-{client_key(client)[:20]}-{Client.objects.count() + 1:05d}", nom=client, kind=request.data.get("client_kind", Client.Kind.PERSON))
        DossierParty.objects.create(dossier=dossier, client=client_record, role="client_principal", is_primary=True)
        for party in request.data.get("parties", []):
            party_name = str(party.get("nom", "")).strip()
            if not party_name:
                continue
            record, _ = Client.objects.get_or_create(
                nom=party_name,
                defaults={"reference": f"CLI-{client_key(party_name)[:20]}-{Client.objects.count() + 1:05d}", "kind": party.get("kind", Client.Kind.PERSON)},
            )
            DossierParty.objects.get_or_create(dossier=dossier, client=record, role=str(party.get("role", "partie"))[:80], defaults={"relationship": str(party.get("relationship", ""))[:120]})
        log_event(request, "dossier_created", "dossier", str(dossier.pk))
        return Response({"reference": dossier.reference, "nom": dossier.nom, "client": dossier.client, "domaine": dossier.domaine, "statut": dossier.statut, "niveauDeConfidentialite": dossier.niveau_de_confidentialite}, status=status.HTTP_201_CREATED)

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
        from documents.serializers import DocumentSerializer
        visible_docs = [doc for doc in item.documents.filter(is_archived=False).select_related("uploaded_by", "validated_by") if has_document_access(request.user, doc)]
        documents = DocumentSerializer(visible_docs, many=True, context={"request": request}).data
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
        "checklist": [{"id": c.id, "label": c.label, "required": c.required, "completed": bool(c.completed_at), "completedAt": c.completed_at.isoformat() if c.completed_at else None} for c in item.checklist_items.all()],
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
        if level is not None:
            if request.user.role != "admin":
                return Response({"detail": "Seul le notaire peut modifier la confidentialité d'un dossier."}, status=403)
            if level not in Dossier.Confidentiality.values:
                return Response({"niveau_de_confidentialite": ["Valeur invalide."]}, status=400)
            dossier.niveau_de_confidentialite = level
            changed.append("niveau_de_confidentialite")
            # The folder policy is the source of truth for its documents.
            dossier.documents.filter(is_archived=False).update(niveau_de_confidentialite=level)
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
            dossier.statut = next_status
            changed.append("statut")
        if not changed:
            return Response({"detail": "Aucune modification demandée."}, status=400)
        dossier.save(update_fields=changed)
        log_event(request, "dossier_legal_hold_updated" if "legal_hold" in changed else "dossier_confidentiality_updated", "dossier", str(dossier.pk), metadata={"fields": changed})
        return Response({"ok": True, "reference": dossier.reference, "niveauDeConfidentialite": dossier.niveau_de_confidentialite, "legalHold": dossier.legal_hold})


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
        check = DossierChecklistItem.objects.create(dossier=item, label=label[:255], required=bool(request.data.get("required", True)))
        log_event(request, "dossier_checklist_item_created", "dossier", item.reference, metadata={"item": check.id})
        return Response({"id": check.id}, status=201)

    def patch(self, request, reference):
        item = dossier_or_404(request, reference)
        check = DossierChecklistItem.objects.filter(dossier=item, pk=request.data.get("id")).first() if item else None
        if not check:
            return Response({"detail": "Élément de checklist introuvable."}, status=404)
        completed = bool(request.data.get("completed"))
        check.completed_at = timezone.now() if completed else None
        check.completed_by = request.user if completed else None
        check.save(update_fields=["completed_at", "completed_by"])
        log_event(request, "dossier_checklist_item_completed" if completed else "dossier_checklist_item_reopened", "dossier", item.reference, metadata={"item": check.id})
        return Response({"ok": True, "completed": completed})


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
    def get(self, request, reference):
        item = dossier_or_404(request, reference)
        if not item:
            return Response({"detail": "Dossier introuvable ou accès refusé."}, status=404)
        from documents.models import Document
        from audit.models import AuditLog
        output = io.BytesIO()
        docs = Document.objects.filter(dossier=item).order_by("created_at")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadonnees.json", json.dumps(dossier_payload(item), ensure_ascii=False, indent=2))
            bordereau = io.StringIO()
            writer = csv.writer(bordereau)
            writer.writerow(["Référence", "Référence métier", "Nom", "Version", "SHA-256", "Statut", "Confidentialité"])
            for doc in docs:
                writer.writerow([doc.reference, doc.code_notarial_actuel(), doc.nom, doc.version, doc.sha256, doc.statut, doc.niveau_de_confidentialite])
                if has_dossier_access(request.user, item):
                    safe_name = os.path.basename(doc.original_filename).replace("\\", "_")
                    archive.writestr(f"Documents/{doc.reference}_{safe_name}", doc.decrypted_bytes())
            archive.writestr("bordereau.csv", bordereau.getvalue())
            audit_rows = AuditLog.objects.filter(target_id__in=[item.reference, *docs.values_list("reference", flat=True)]).order_by("timestamp")
            archive.writestr("historique.json", json.dumps([{"date": row.timestamp.isoformat(), "action": row.action, "cible": row.target_id, "résultat": row.result, "empreinte": row.entry_hash} for row in audit_rows], ensure_ascii=False, indent=2))
        log_event(request, "dossier_exported", "dossier", item.reference)
        response = HttpResponse(output.getvalue(), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="{item.reference}.zip"'
        return response
