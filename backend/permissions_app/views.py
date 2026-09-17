from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from accounts.models import User
from documents.models import Document
from dossiers.models import Dossier
from notifications.services import notify, notify_admins
from .models import AccessRequest, Permission


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


class PermissionView(APIView):
    def get(self, request):
        if request.user.role != "admin": return Response({"detail": "Réservé au notaire."}, status=403)
        items = Permission.objects.select_related("user", "document", "dossier", "granted_by").order_by("-created_at")
        return Response([
            {
                "id": p.id,
                "beneficiaire": p.user.display_name,
                "beneficiaireEmail": p.user.email,
                "cible": p.document.nom if p.document_id else (p.dossier.nom if p.dossier_id else "—"),
                "cibleReference": p.document.reference if p.document_id else (p.dossier.reference if p.dossier_id else None),
                "accessLevel": p.access_level,
                "accessLevelLabel": p.get_access_level_display(),
                "grantedBy": p.granted_by.display_name,
                "reason": p.reason,
                "createdAt": p.created_at.isoformat(),
            }
            for p in items
        ])

    def post(self, request):
        if request.user.role != "admin": return Response({"detail": "Réservé au notaire."}, status=403)
        user = User.objects.filter(email__iexact=request.data.get("email", "")).first() or User.objects.filter(pk=request.data.get("user")).first()
        document = Document.objects.filter(reference=request.data.get("ref") or request.data.get("document")).first()
        dossier = Dossier.objects.filter(reference=request.data.get("dossier")).first()
        if not user or not (document or dossier): return Response({"detail": "Bénéficiaire et cible requis."}, status=400)
        access_level = request.data.get("accessLevel", request.data.get("niveau_acces", "lecture"))
        if access_level not in Permission.Level.values: return Response({"accessLevel": ["Niveau d'accès invalide."]}, status=400)
        item = Permission.objects.create(user=user, document=document, dossier=dossier, access_level=access_level, granted_by=request.user, reason=request.data.get("motif", request.data.get("motif_obligatoire", "")))
        log_event(request, "permission_granted", "permission", str(item.pk))
        cible = document.nom if document else (dossier.nom if dossier else "—")
        notify(
            user,
            "permission_granted",
            "Accès accordé",
            f"{request.user.display_name} vous a accordé un accès en {item.get_access_level_display().lower()} sur « {cible} ».",
        )
        return Response({"ok": True, "id": item.id}, status=201)

    def delete(self, request):
        if request.user.role != "admin": return Response({"detail": "Réservé au notaire."}, status=403)
        qs = Permission.objects.all()
        if request.data.get("id"): qs = qs.filter(pk=request.data["id"])
        elif request.data.get("beneficiaire"): qs = qs.filter(user__email__iexact=request.data["beneficiaire"])
        count, _ = qs.delete()
        log_event(request, "permission_revoked", result="success")
        return Response({"ok": True, "deleted": count})


def access_request_payload(item: AccessRequest) -> dict:
    return {
        "id": item.id,
        "demandeur": item.requester.display_name,
        "demandeurEmail": item.requester.email,
        "cible": item.document.nom if item.document_id else (item.dossier.nom if item.dossier_id else "—"),
        "cibleReference": item.document.reference if item.document_id else (item.dossier.reference if item.dossier_id else None),
        "motif": item.reason,
        "status": item.status,
        "statusLabel": item.get_status_display(),
        "processedBy": item.processed_by.display_name if item.processed_by_id else None,
        "createdAt": item.created_at.isoformat(),
    }


class AccessRequestView(APIView):
    def get(self, request):
        # A clerc/collaborateur sees their own requests; the notaire sees every request awaiting a decision.
        if request.user.role == "admin":
            items = AccessRequest.objects.select_related("requester", "document", "dossier").order_by("status", "-created_at")
        else:
            items = AccessRequest.objects.filter(requester=request.user).select_related("document", "dossier").order_by("-created_at")
        return Response([access_request_payload(item) for item in items])

    def post(self, request):
        ref = request.data.get("ref") or request.data.get("document")
        document = Document.objects.filter(reference=ref).first()
        dossier = Dossier.objects.filter(reference=request.data.get("dossier")).first() if not document else None
        if not document and not dossier: return Response({"detail": "Document ou dossier introuvable."}, status=404)
        reason = request.data.get("motif") or request.data.get("reason")
        if not reason: return Response({"motif": ["Le motif est obligatoire."]}, status=400)
        existing = AccessRequest.objects.filter(
            requester=request.user, document=document, dossier=dossier, status=AccessRequest.Status.PENDING,
        ).first()
        if existing:
            return Response(access_request_payload(existing), status=200)
        item = AccessRequest.objects.create(requester=request.user, document=document, dossier=dossier, reason=reason)
        log_event(request, "access_requested", "document" if document else "dossier", document.reference if document else dossier.reference)
        cible = document.nom if document else (dossier.nom if dossier else "—")
        notify_admins(
            "access_request",
            "Nouvelle demande d'accès",
            f"{request.user.display_name} demande un accès à « {cible} ». Motif : {reason}",
        )
        return Response({"ok": True, "id": item.id}, status=201)

    def patch(self, request):
        # Seul le notaire décide : accepter une demande crée automatiquement le
        # droit d'accès correspondant (traçable, motivé) ; refuser la clôture sans rien ouvrir.
        if request.user.role != "admin": return Response({"detail": "Réservé au notaire."}, status=403)
        item = AccessRequest.objects.select_related("requester", "document", "dossier").filter(pk=request.data.get("id")).first()
        if not item: return Response({"detail": "Demande introuvable."}, status=404)
        if item.status != AccessRequest.Status.PENDING: return Response({"detail": "Cette demande a déjà été traitée."}, status=409)
        decision = request.data.get("decision") or request.data.get("action")
        if decision not in {"accept", "refuse"}: return Response({"decision": ["Valeur attendue : accept ou refuse."]}, status=400)
        item.processed_by = request.user
        cible = item.document.nom if item.document_id else (item.dossier.nom if item.dossier_id else "—")
        if decision == "accept":
            item.status = AccessRequest.Status.ACCEPTED
            item.save(update_fields=["status", "processed_by"])
            permission = Permission.objects.create(
                user=item.requester, document=item.document, dossier=item.dossier,
                access_level=request.data.get("accessLevel", "lecture"), granted_by=request.user,
                reason=item.reason,
            )
            log_event(request, "access_request_accepted", "permission", str(permission.pk))
            notify(
                item.requester,
                "access_request",
                "Demande d'accès acceptée",
                f"{request.user.display_name} a accepté votre demande d'accès à « {cible} ».",
            )
        else:
            item.status = AccessRequest.Status.REFUSED
            item.save(update_fields=["status", "processed_by"])
            log_event(request, "access_request_refused", "access_request", str(item.pk))
            notify(
                item.requester,
                "access_request",
                "Demande d'accès refusée",
                f"{request.user.display_name} a refusé votre demande d'accès à « {cible} ».",
            )
        return Response({"ok": True, "status": item.status})
