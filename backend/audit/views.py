import csv
import hashlib
import json
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from .models import AuditLog
from ged_backend.api import ContractSerializer
from .services import log_event, verify_chain


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


MAX_LIGNES = 500


def _journal(request):
    """Périmètre + filtres communs à la consultation et à l'export.

    Renvoie `(queryset, erreur)`. Un journal qu'on ne peut ni filtrer par
    période, ni par acteur, ni par action n'est pas exploitable pour un
    contrôle : on expose donc ces trois axes, sur le même périmètre que la
    lecture (le sien par défaut, le cabinet pour le notaire).
    """
    scope = request.query_params.get("scope", "me")
    if scope == "cabinet" and request.user.role != "admin":
        return None, Response({"detail": "Journal du cabinet réservé au notaire."}, status=403)
    logs = AuditLog.objects.all() if scope == "cabinet" else AuditLog.objects.filter(user=request.user)
    if request.query_params.get("action"):
        logs = logs.filter(action__icontains=request.query_params["action"].strip())
    if request.query_params.get("cible"):
        logs = logs.filter(target_id__icontains=request.query_params["cible"].strip())
    if request.query_params.get("acteur"):
        logs = logs.filter(user__email__icontains=request.query_params["acteur"].strip())
    if request.query_params.get("resultat"):
        logs = logs.filter(result=request.query_params["resultat"].strip())
    if request.query_params.get("date_from"):
        logs = logs.filter(timestamp__date__gte=request.query_params["date_from"])
    if request.query_params.get("date_to"):
        logs = logs.filter(timestamp__date__lte=request.query_params["date_to"])
    return logs.select_related("user"), None


class AuditExportView(APIView):
    def get(self, request):
        logs, erreur = _journal(request)
        if erreur is not None:
            return erreur
        scope = request.query_params.get("scope", "me")
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = "attachment; filename=audit.csv"
        writer = csv.writer(response)
        # « details » porte l'ancienne et la nouvelle valeur : le cahier des
        # charges les exige, elles étaient écrites en base et ne sortaient
        # d'aucune API — le journal n'était donc pas exploitable pour un audit.
        writer.writerow(["timestamp", "user", "action", "target_type", "target_id", "ip", "result", "details", "entry_hash"])
        for log in logs.iterator():
            writer.writerow([
                log.timestamp.isoformat(), log.user.email if log.user else "", log.action,
                log.target_type, log.target_id, log.ip_address or "", log.result,
                json.dumps(log.metadata or {}, ensure_ascii=False, sort_keys=True),
                log.entry_hash,
            ])
        log_event(request, "audit_export", "audit", scope)
        return response


class AuditListView(APIView):
    """Read audit records for the live journal page, respecting its scope."""
    def get(self, request):
        logs, erreur = _journal(request)
        if erreur is not None:
            return erreur
        try:
            limite = min(int(request.query_params.get("limit", 200)), MAX_LIGNES)
        except (TypeError, ValueError):
            limite = 200
        total = logs.count()
        lignes = [{
            "id": log.id, "timestamp": log.timestamp.isoformat(), "user": log.user.display_name if log.user else "Système",
            "action": log.action, "targetType": log.target_type, "targetId": log.target_id, "result": log.result,
            "ip": log.ip_address or "", "details": log.metadata or {}, "entryHash": log.entry_hash,
        } for log in logs[:limite]]
        return Response({"total": total, "returned": len(lignes), "entries": lignes})


def alert_payload(alerte) -> dict:
    return {
        "id": alerte.pk, "rule": alerte.rule, "severity": alerte.severity, "title": alerte.title,
        "message": alerte.message, "status": alerte.status, "details": alerte.details,
        "subject": alerte.subject_user.display_name if alerte.subject_user_id else None,
        "createdAt": alerte.created_at.isoformat(),
        "handledBy": alerte.handled_by.display_name if alerte.handled_by_id else None,
        "handledAt": alerte.handled_at.isoformat() if alerte.handled_at else None,
        "handlingNote": alerte.handling_note,
    }


class SecurityAlertListView(APIView):
    """Alertes de la surveillance automatique (notaire uniquement)."""

    def get(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Réservé au notaire."}, status=403)
        from .models import SecurityAlert
        alertes = SecurityAlert.objects.select_related("subject_user", "handled_by")
        if request.query_params.get("status") in SecurityAlert.Status.values:
            alertes = alertes.filter(status=request.query_params["status"])
        try:
            limite = min(int(request.query_params.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limite = 100
        return Response({"open": SecurityAlert.objects.filter(status=SecurityAlert.Status.OPEN).count(),
                         "entries": [alert_payload(a) for a in alertes[:limite]]})


class SecurityAlertDetailView(APIView):
    def patch(self, request, pk):
        if request.user.role != "admin":
            return Response({"detail": "Réservé au notaire."}, status=403)
        from django.utils import timezone
        from .models import SecurityAlert
        alerte = SecurityAlert.objects.filter(pk=pk).first()
        if not alerte:
            return Response({"detail": "Alerte introuvable."}, status=404)
        if request.data.get("status") != SecurityAlert.Status.HANDLED:
            return Response({"status": ["Seule la valeur « traitée » est acceptée."]}, status=400)
        note = str(request.data.get("note", "")).strip()
        if not note:
            return Response({"note": ["Indiquez ce qui a été vérifié ou décidé."]}, status=400)
        if alerte.status == SecurityAlert.Status.HANDLED:
            return Response({"detail": "Alerte déjà traitée."}, status=409)
        alerte.status, alerte.handled_by, alerte.handled_at, alerte.handling_note = SecurityAlert.Status.HANDLED, request.user, timezone.now(), note[:2000]
        alerte.save(update_fields=["status", "handled_by", "handled_at", "handling_note"])
        log_event(request, "security_alert_handled", "security_alert", str(alerte.pk), metadata={"note": note[:300], "regle": alerte.rule})
        return Response(alert_payload(alerte))


class AuditIntegrityView(APIView):
    """Verify the append-only chain without ever exposing a write path."""
    def get(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Contrôle d'intégrité réservé au notaire."}, status=403)
        resultat = verify_chain()
        if not resultat["ok"]:
            return Response(resultat, status=409)
        log_event(request, "audit_integrity_checked", "audit", "cabinet", metadata={"checked": resultat["checked"]})
        return Response(resultat)
