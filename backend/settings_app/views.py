import hashlib
import json
from pathlib import Path
from django.conf import settings
from django.db.models import Sum
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.generics import GenericAPIView
from audit.services import log_event
from ged_backend.api import ContractSerializer
from documents.models import Document
from .models import BackupRun, CabinetSettings
from .backup_service import run_backup


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


class SettingsView(APIView):
    def get(self, request):
        if request.user.role != "admin": return Response({"detail": "Configuration réservée au notaire."}, status=403)
        item, _ = CabinetSettings.objects.get_or_create(pk=1)
        return Response({
            "cabinet_name": item.cabinet_name,
            "codification_policy": item.codification_policy or "ISO15489-{ANNÉE}-{DOSSIER}-{SÉQUENCE}",
            "storage_mode": item.data.get("storage_mode", "local"),
            "city": item.data.get("city", ""),
            "updatedAt": item.updated_at.isoformat(),
            "praPca": item.data.get("pra_pca", {"rtoMinutes":240,"rpoMinutes":60,"recoveryOrder":["database","keys","documents","rights","audit"]}),
        })

    def put(self, request):
        if request.user.role != "admin": return Response({"detail": "Configuration réservée au notaire."}, status=403)
        item, _ = CabinetSettings.objects.get_or_create(pk=1)
        item.cabinet_name = request.data.get("cabinet_name", request.data.get("nom_cabinet", item.cabinet_name))
        item.codification_policy = request.data.get("codification_policy", request.data.get("politique_codification", item.codification_policy))
        accepted = {key: request.data[key] for key in ("city", "storage_mode") if key in request.data}
        if "praPca" in request.data:
            pra = request.data["praPca"]
            if not isinstance(pra, dict) or int(pra.get("rtoMinutes", 0)) <= 0 or int(pra.get("rpoMinutes", 0)) <= 0:
                return Response({"praPca": ["RTO/RPO doivent être des durées positives."]}, status=400)
            accepted["pra_pca"] = pra
        if accepted.get("storage_mode") not in {None, "local", "cloud", "hybrid"}:
            return Response({"storage_mode": ["Valeur invalide."]}, status=400)
        item.data = {**item.data, **accepted}
        item.save()
        log_event(request, "settings_updated", "settings", "cabinet")
        return Response({"ok": True, "cabinet_name": item.cabinet_name, "codification_policy": item.codification_policy})


class RestoreTestView(APIView):
    def post(self, request):
        if request.user.role != "admin": return Response({"detail": "Sauvegarde réservée au notaire."}, status=403)
        documents = Document.objects.exclude(statut=Document.Status.DESTROYED).exclude(fichier="").all().only("id", "fichier", "sha256", "size_bytes", "encryption_key_id")
        latest_backup = BackupRun.objects.filter(kind="backup", status=BackupRun.Status.SUCCESS).order_by("-created_at").first()
        backup_checked = False
        if latest_backup and latest_backup.local_path:
            backup_root = Path(settings.BASE_DIR) / latest_backup.local_path
            manifest_path, db_path = backup_root / "manifest.json", backup_root / "database.json"
            if not manifest_path.exists() or not db_path.exists():
                failure = "La dernière sauvegarde complète est incomplète (manifest ou base absente)."
            else:
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    expected = manifest.get("database", {}).get("sha256")
                    if expected and hashlib.sha256(db_path.read_bytes()).hexdigest() != expected:
                        raise ValueError("empreinte de la base de sauvegarde différente")
                    json.loads(db_path.read_text(encoding="utf-8"))
                    backup_checked = True
                except Exception as exc:
                    failure = f"Sauvegarde non restaurable : {exc}"
        checked = 0
        checked_bytes = 0
        failure = locals().get("failure", "")
        for document in documents.iterator():
            try:
                content = document.decrypted_bytes()
                if hashlib.sha256(content).hexdigest() != document.sha256:
                    raise ValueError("empreinte SHA-256 différente")
                checked += 1
                checked_bytes += len(content)
            except Exception as exc:
                failure = f"Document {document.pk} non restaurable : {exc}"
                break
        run = BackupRun.objects.create(
            status=BackupRun.Status.FAILURE if failure else BackupRun.Status.SUCCESS,
            checked_documents=checked,
            checked_bytes=checked_bytes,
            message=failure or ("Dernière sauvegarde complète vérifiée (base + fichiers) et tous les documents de production déchiffrables ; aucune donnée de production modifiée." if backup_checked else "Aucune sauvegarde complète disponible : contrôle des documents de production uniquement."),
            requested_by=request.user, kind="restore_test",
            local_path=latest_backup.local_path if latest_backup else "", cloud_status=latest_backup.cloud_status if latest_backup else "not_configured",
        )
        log_event(request, "backup_restore_test_completed", "backup", str(run.pk), result="failure" if failure else "success")
        return Response({"ok": not bool(failure), "status": run.status, "message": run.message, "run": backup_run_payload(run)}, status=200 if not failure else 409)


def backup_run_payload(run):
    return {
        "id": run.pk,
        "status": run.status,
        "checkedDocuments": run.checked_documents,
        "checkedBytes": run.checked_bytes,
        "message": run.message,
        "kind": run.kind,
        "localPath": run.local_path,
        "cloudStatus": run.cloud_status,
        "createdAt": run.created_at.isoformat(),
    }


class RunBackupView(APIView):
    def post(self, request):
        if request.user.role != "admin":
            return Response({"detail": "Sauvegarde réservée au notaire."}, status=403)
        try:
            run = run_backup(request.user)
        except Exception as exc:
            log_event(request, "backup_run_failed", "backup", "manual", result="failure", metadata={"error": str(exc)[:500]})
            return Response({"ok": False, "detail": str(exc)}, status=503)
        log_event(request, "backup_run_completed", "backup", str(run.pk), metadata={"cloudStatus": run.cloud_status})
        return Response({"ok": True, "run": backup_run_payload(run)})


class BackupStatusView(APIView):
    def get(self, request):
        if request.user.role != "admin": return Response({"detail": "Sauvegarde réservée au notaire."}, status=403)
        total_bytes = Document.objects.aggregate(total=Sum("size_bytes"))["total"] or 0
        latest = BackupRun.objects.order_by("-created_at").first()
        history = BackupRun.objects.order_by("-created_at")[:20]
        cloud_enabled = bool(getattr(settings, "BACKUP_CLOUD_ENABLED", False))
        cloud_configured = bool(cloud_enabled and getattr(settings, "BACKUP_CLOUD_BUCKET", "") and getattr(settings, "BACKUP_CLOUD_ACCESS_KEY", "") and getattr(settings, "BACKUP_CLOUD_SECRET_KEY", ""))
        last_backup = BackupRun.objects.filter(kind="backup").order_by("-created_at").first()
        return Response({
            "local": {"usedBytes": total_bytes, "documentCount": Document.objects.exclude(statut=Document.Status.DESTROYED).count(), "status": "ready"},
            "cloud": {"configured": cloud_configured, "status": "ready" if cloud_configured else "not_configured"},
            "lastBackup": backup_run_payload(last_backup) if last_backup else None,
            "lastRestoreTest": backup_run_payload(latest) if latest else None,
            "history": [backup_run_payload(run) for run in history],
        })


class FilialeContextView(APIView):
    def post(self, request):
        request.user.active_filiale = str(request.data.get("name", ""))[:150]
        request.user.save(update_fields=["active_filiale"])
        log_event(request, "filiale_context_changed", "filiale", request.user.active_filiale)
        return Response({"ok": True, "name": request.user.active_filiale})
