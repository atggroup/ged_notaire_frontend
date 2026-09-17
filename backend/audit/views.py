import csv
import hashlib
import json
from django.http import HttpResponse
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from .models import AuditLog
from ged_backend.api import ContractSerializer
from .services import log_event


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


class AuditExportView(APIView):
    def get(self, request):
        scope = request.query_params.get("scope", "me")
        if scope == "cabinet" and request.user.role != "admin":
            return Response({"detail": "Export cabinet réservé au notaire."}, status=403)
        logs = AuditLog.objects.all() if scope == "cabinet" else AuditLog.objects.filter(user=request.user)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = "attachment; filename=audit.csv"
        writer = csv.writer(response)
        writer.writerow(["timestamp", "user", "action", "target_type", "target_id", "ip", "result"])
        for log in logs:
            writer.writerow([log.timestamp.isoformat(), log.user.email if log.user else "", log.action, log.target_type, log.target_id, log.ip_address or "", log.result])
        log_event(request, "audit_export", "audit", scope)
        return response


class AuditListView(APIView):
    """Read audit records for the live journal page, respecting its scope."""
    def get(self, request):
        scope = request.query_params.get("scope", "me")
        if scope == "cabinet" and request.user.role != "admin":
            return Response({"detail": "Lecture cabinet réservée au notaire."}, status=403)
        logs = AuditLog.objects.all() if scope == "cabinet" else AuditLog.objects.filter(user=request.user)
        return Response([{
            "id": log.id, "timestamp": log.timestamp.isoformat(), "user": log.user.display_name if log.user else "Système",
            "action": log.action, "targetType": log.target_type, "targetId": log.target_id, "result": log.result,
        } for log in logs[:200]])


class AuditIntegrityView(APIView):
    """Verify the append-only chain without ever exposing a write path."""
    def get(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Contrôle d'intégrité réservé au notaire."}, status=403)
        previous_hash = ""
        checked = 0
        for log in AuditLog.objects.order_by("id").iterator():
            # Records created before the migration are readable but could not
            # have a historic hash. Every later record is verified in order.
            if not log.entry_hash:
                continue
            payload = {
                "timestamp": log.timestamp.isoformat(), "user_id": log.user_id,
                "action": log.action, "target_type": log.target_type, "target_id": log.target_id,
                "ip_address": log.ip_address, "result": log.result, "metadata": log.metadata or {},
                "previous_hash": previous_hash,
            }
            expected = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
            if log.previous_hash != previous_hash or log.entry_hash != expected:
                return Response({"ok": False, "checked": checked, "invalidLogId": log.pk}, status=409)
            previous_hash = log.entry_hash
            checked += 1
        log_event(request, "audit_integrity_checked", "audit", "cabinet", metadata={"checked": checked})
        return Response({"ok": True, "checked": checked})
