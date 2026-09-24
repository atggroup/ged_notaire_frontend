from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from accounts.models import User
from accounts.services import reevaluer_exigence_mfa
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
        # Un accès confidentiel accordé à une session ouverte sans second
        # facteur doit forcer une reconnexion, sinon la politique MFA est
        # contournée pendant toute la durée de vie du jeton.
        reevaluer_exigence_mfa(user, document, dossier, request=request)
        cible = document.nom if document else (dossier.nom if dossier else "—")
        notify(
            user,
            "permission_granted",
            "Accès accordé",
            f"{request.user.display_name} vous a accordé un accès en {item.get_access_level_display().lower()} sur « {cible} ».",
        )
        return Response({"ok": True, "id": item.id}, status=201)

    def delete(self, request):
        """Révocation d'habilitation — toujours ciblée, jamais en masse.

        Sans critère, la vue partait de `Permission.objects.all()` et un appel
        au corps vide effaçait TOUTES les habilitations de l'étude ; l'entrée
        d'audit, sans cible ni décompte, ne permettait même pas de savoir ce
        qui avait été retiré.
        """
        if request.user.role != "admin": return Response({"detail": "Réservé au notaire."}, status=403)
        if request.data.get("id"):
            qs = Permission.objects.filter(pk=request.data["id"])
        elif request.data.get("beneficiaire"):
            qs = Permission.objects.filter(user__email__iexact=request.data["beneficiaire"])
        else:
            return Response({"detail": "Précisez l'habilitation à révoquer (id) ou le bénéficiaire concerné."}, status=400)
        revoquees = [
            {
                "id": p.pk,
                "beneficiaire": p.user.email,
                "cible": p.document.reference if p.document_id else (p.dossier.reference if p.dossier_id else None),
                "niveau": p.access_level,
            }
            for p in qs.select_related("user", "document", "dossier")
        ]
        if not revoquees:
            return Response({"detail": "Aucune habilitation ne correspond."}, status=404)
        count, _ = qs.delete()
        log_event(request, "permission_revoked", "permission", str(request.data.get("id", request.data.get("beneficiaire", ""))), metadata={"revoquees": revoquees, "nombre": count})
        for ligne in revoquees:
            beneficiaire = User.objects.filter(email__iexact=ligne["beneficiaire"]).first()
            notify(
                beneficiaire, "permission_revoked", "Accès retiré",
                f"{request.user.display_name} a retiré votre accès à « {ligne['cible'] or '—'} ».",
            )
        return Response({"ok": True, "deleted": count})


def access_request_payload(item: AccessRequest, *, pour_notaire: bool = True) -> dict:
    reference = item.document.reference if item.document_id else (item.dossier.reference if item.dossier_id else None)
    intitule = item.document.nom if item.document_id else (item.dossier.nom if item.dossier_id else "—")
    # Le demandeur n'a, par définition, pas encore accès : lui montrer
    # l'intitulé (« Divorce Y », « Succession X ») divulguerait ce que
    # l'habilitation protège. Il ne voit que la référence tant que ce n'est
    # pas accordé.
    if not pour_notaire and item.status != AccessRequest.Status.ACCEPTED:
        intitule = reference or "—"
    return {
        "id": item.id,
        "demandeur": item.requester.display_name,
        "demandeurEmail": item.requester.email,
        "cible": intitule,
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
        return Response([access_request_payload(item, pour_notaire=request.user.role == "admin") for item in items])

    def post(self, request):
        ref = request.data.get("ref") or request.data.get("document")
        document = Document.objects.filter(reference=ref).first()
        dossier = Dossier.objects.filter(reference=request.data.get("dossier")).first() if not document else None
        reason = request.data.get("motif") or request.data.get("reason")
        if not reason: return Response({"motif": ["Le motif est obligatoire."]}, status=400)
        if not document and not dossier:
            # Réponse identique à une demande enregistrée : sinon, les
            # références de dossiers (séquentielles) se sondent une à une.
            # La tentative est tracée pour la surveillance.
            log_event(request, "access_request_unknown_target", "dossier", str(request.data.get("dossier") or ref or "")[:80], result="failure")
            return Response({"ok": True, "detail": "Demande transmise au notaire."}, status=201)
        existing = AccessRequest.objects.filter(
            requester=request.user, document=document, dossier=dossier, status=AccessRequest.Status.PENDING,
        ).first()
        if existing:
            return Response({"ok": True, "detail": "Demande transmise au notaire."}, status=201)
        item = AccessRequest.objects.create(requester=request.user, document=document, dossier=dossier, reason=reason)
        log_event(request, "access_requested", "document" if document else "dossier", document.reference if document else dossier.reference)
        cible = document.nom if document else (dossier.nom if dossier else "—")
        notify_admins(
            "access_request",
            "Nouvelle demande d'accès",
            f"{request.user.display_name} demande un accès à « {cible} ». Motif : {reason}",
        )
        return Response({"ok": True, "detail": "Demande transmise au notaire."}, status=201)

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
            reevaluer_exigence_mfa(item.requester, item.document, item.dossier, request=request)
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
