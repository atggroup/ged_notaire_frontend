"""Real backup service for encrypted GED files.

The application stores document binaries encrypted at rest. A backup therefore
copies the ciphertext, plus a manifest of the metadata needed to identify and
verify every copy. An optional S3-compatible target provides an off-site copy.
Secrets/keys are never written to the backup; their key IDs are recorded so
operators know which secret generation must be recovered separately.
"""
import hashlib
import json
import os
import shutil
from django.core.management import call_command
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from documents.models import Document
from .models import BackupRun


def _local_root() -> Path:
    root = Path(getattr(settings, "BACKUP_LOCAL_ROOT", "" ) or (Path(settings.BASE_DIR) / "backup_store"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _relative_file(doc: Document) -> str:
    name = doc.fichier.name or f"documents/{doc.reference}.enc"
    return name.replace("\\", "/")


def _manifest(documents):
    return {
        "format": "GED-BACKUP-1",
        "createdAt": timezone.now().isoformat(),
        "encryption": {
            "algorithm": "AES-256-GCM",
            "keyIds": sorted({d.encryption_key_id or "legacy" for d in documents}),
            "keyMaterialIncluded": False,
            "keyRecovery": "Les clés doivent être restaurées séparément depuis le gestionnaire de secrets du cabinet.",
        },
        "documents": [
            {
                "reference": d.reference,
                "masterReference": d.id_maitre,
                "codeNotarial": d.code_notarial,
                "version": d.version,
                "sha256": d.sha256,
                "encryptedSha256": _encrypted_hash(d),
                "sizeBytes": d.size_bytes,
                "filename": d.original_filename,
                "contentType": d.content_type,
                "status": d.statut,
                "dossier": d.dossier.reference if d.dossier_id else None,
                "encryptionKeyId": d.encryption_key_id or "legacy",
                "storedPath": _relative_file(d),
            }
            for d in documents
        ],
    }


def _encrypted_hash(doc: Document) -> str:
    digest = hashlib.sha256()
    with doc.fichier.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_documents(documents, root: Path) -> tuple[int, int]:
    count = 0
    total = 0
    for doc in documents:
        if not doc.fichier:
            continue
        target = root / _relative_file(doc)
        target.parent.mkdir(parents=True, exist_ok=True)
        with doc.fichier.open("rb") as src, target.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
                total += len(chunk)
        # Verify the copied ciphertext immediately.
        src_hash = _encrypted_hash(doc)
        copied_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if src_hash != copied_hash:
            raise ValueError(f"Copie de sauvegarde corrompue pour {doc.reference}.")
        count += 1
    return count, total


def _stored_local_path(root: Path) -> str:
    """Return a portable path for the BackupRun record.

    BACKUP_LOCAL_ROOT may intentionally point outside BASE_DIR (for example an
    external disk or a temporary/test directory), so Path.relative_to() must
    not be allowed to turn a successful backup into a 503 response.
    """
    try:
        return str(root.relative_to(settings.BASE_DIR))
    except ValueError:
        return str(root)


def _cloud_configured() -> bool:
    return bool(
        getattr(settings, "BACKUP_CLOUD_ENABLED", False)
        and getattr(settings, "BACKUP_CLOUD_BUCKET", "")
        and getattr(settings, "BACKUP_CLOUD_ACCESS_KEY", "")
        and getattr(settings, "BACKUP_CLOUD_SECRET_KEY", "")
    )


def _upload_cloud(root: Path) -> int:
    if not _cloud_configured():
        raise RuntimeError("Le stockage cloud n'est pas complètement configuré.")
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 n'est pas installé : impossible d'utiliser la sauvegarde S3.") from exc

    client = boto3.client(
        "s3",
        region_name=getattr(settings, "BACKUP_CLOUD_REGION", "us-east-1"),
        endpoint_url=getattr(settings, "BACKUP_CLOUD_ENDPOINT", "") or None,
        aws_access_key_id=settings.BACKUP_CLOUD_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_CLOUD_SECRET_KEY,
    )
    prefix = (getattr(settings, "BACKUP_CLOUD_PREFIX", "ged") or "ged").strip("/")
    uploaded = 0
    for path in root.rglob("*"):
        if path.is_file():
            key = f"{prefix}/{path.relative_to(root).as_posix()}"
            client.upload_file(str(path), settings.BACKUP_CLOUD_BUCKET, key)
            uploaded += 1
    return uploaded


def run_backup(request_user):
    documents = list(Document.objects.exclude(statut=Document.Status.DESTRUCTION_AUTHORIZED).exclude(statut=getattr(Document.Status, "DESTROYED", "__never__")).select_related("dossier"))
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    root = _local_root() / timestamp
    root.mkdir(parents=True, exist_ok=True)
    try:
        count, total = _copy_documents(documents, root)
        # Logical database snapshot contains accounts, rights, metadata and audit logs.
        db_path = root / "database.json"
        with db_path.open("w", encoding="utf-8") as db_file:
            call_command("dumpdata", indent=2, stdout=db_file)
        manifest = _manifest(documents)
        manifest["database"] = {"format": "django-dumpdata-json", "file": "database.json", "sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(), "sizeBytes": db_path.stat().st_size}
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        cloud_uploaded = 0
        cloud_status = "not_configured"
        if _cloud_configured():
            cloud_uploaded = _upload_cloud(root)
            cloud_status = "success"
        run = BackupRun.objects.create(
            status=BackupRun.Status.SUCCESS,
            checked_documents=count,
            checked_bytes=total,
            message=f"Sauvegarde complète créée : {count} document(s) + base de données. Cloud : {cloud_status} ({cloud_uploaded} fichier(s) transféré(s)).",
            requested_by=request_user,
            kind="backup",
            local_path=_stored_local_path(root),
            cloud_status=cloud_status,
        )
        return run
    except Exception as exc:
        run = BackupRun.objects.create(
            status=BackupRun.Status.FAILURE,
            checked_documents=0,
            checked_bytes=0,
            message=f"Sauvegarde échouée : {exc}",
            requested_by=request_user,
            kind="backup",
            local_path=_stored_local_path(root),
            cloud_status="failure" if _cloud_configured() else "not_configured",
        )
        raise
