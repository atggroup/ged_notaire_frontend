"""Real backup service for encrypted GED files.

The application stores document binaries encrypted at rest. A backup therefore
copies the ciphertext, plus a manifest of the metadata needed to identify and
verify every copy. An optional S3-compatible target provides an off-site copy.
Secrets/keys are never written to the backup; their key IDs are recorded so
operators know which secret generation must be recovered separately.

L'instantané de la base est lui aussi **chiffré** (``database.json.enc``).
En étude notariale, l'intitulé seul d'un dossier — « Succession X », « Divorce
Y » — relève du secret professionnel au même titre que l'acte : un export en
clair aurait livré, à qui met la main sur le support de sauvegarde, les noms
des dossiers et des clients, le journal d'audit complet et les empreintes de
mots de passe. Les secrets éphémères (codes à usage unique, sessions) sont
exclus de l'export : ils n'ont aucune valeur de restauration et tout leur
intérêt pour un attaquant.
"""
import hashlib
import io
import json
import os
import shutil
from datetime import timedelta
from django.core.management import call_command
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from documents.crypto import encrypt
from documents.models import Document
from .models import BackupRun


# Secrets à durée de vie courte : aucune valeur pour restaurer une étude,
# toute valeur pour qui voudrait rejouer une authentification.
TABLES_EXCLUES = [
    "accounts.otpcode", "sessions.session",
    # Recréées par `migrate` sur la base neuve, avec des identifiants qui
    # peuvent différer : les restaurer telles quelles ferait échouer le
    # chargement (conflits d'unicité). Aucune donnée de l'étude n'y vit.
    "contenttypes", "auth.permission", "auth.group", "admin.logentry",
    # État d'exécution des travaux (verrous, signes de vie) : sans valeur
    # après un sinistre, et nuisible s'il était restauré (faux verrous).
    "core.joblock", "core.workerheartbeat",
]


def _local_root() -> Path:
    root = Path(getattr(settings, "BACKUP_LOCAL_ROOT", "" ) or (Path(settings.BASE_DIR) / "backup_store"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _relative_file(doc: Document) -> str:
    name = doc.fichier.name or f"documents/{doc.reference}.enc"
    return name.replace("\\", "/")


def _manifest(documents):
    """Index de vérification, lisible SANS la clé : uniquement des
    identifiants opaques et des empreintes. Les métadonnées parlantes (nom de
    fichier, code notarial, dossier, statut) vont dans `manifest.details.enc`,
    chiffré : le manifeste en clair livrait jusqu'ici les intitulés des pièces
    à qui mettait la main sur le support de sauvegarde."""
    return {
        "format": "GED-BACKUP-2",
        "createdAt": timezone.now().isoformat(),
        "encryption": {
            "algorithm": "AES-256-GCM",
            "keyIds": sorted({d.encryption_key_id or "legacy" for d in documents}),
            "scope": "binaires documentaires, instantané de la base et métadonnées détaillées",
            "keyMaterialIncluded": False,
            "keyRecovery": "Les clés doivent être restaurées séparément depuis le gestionnaire de secrets du cabinet.",
        },
        "documents": [
            {
                "reference": d.reference,
                "version": d.version,
                "encryptedSha256": _encrypted_hash(d),
                "sizeBytes": d.size_bytes,
                "encryptionKeyId": d.encryption_key_id or "legacy",
                "storedPath": _relative_file(d),
            }
            for d in documents
        ],
    }


def _details(documents) -> list[dict]:
    return [
        {
            "reference": d.reference, "masterReference": d.id_maitre, "codeNotarial": d.code_notarial,
            "sha256": d.sha256, "filename": d.original_filename, "contentType": d.content_type,
            "status": d.statut, "dossier": d.dossier.reference if d.dossier_id else None,
        }
        for d in documents
    ]


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


# Organisation du stockage hors site :
#   <prefix>/documents/...               pièces chiffrées, envoyées UNE fois
#                                        (un fichier chiffré ne change jamais :
#                                        une correction est une nouvelle version,
#                                        un rechiffrement un nouveau fichier)
#   <prefix>/sauvegardes/<horodatage>/   instantané de la base et manifestes
# Recommandé : activer le verrouillage d'objets (Object Lock) du fournisseur :
# même avec les identifiants du serveur, un rançongiciel ne peut alors ni
# effacer ni écraser les copies pendant la durée de rétention.
FICHIERS_INSTANTANE = ("manifest.json", "database.json.enc", "manifest.details.enc")


def _cloud_client():
    if not _cloud_configured():
        raise RuntimeError("Le stockage hors site n'est pas complètement configuré (BACKUP_CLOUD_*).")
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 n'est pas installé : impossible d'utiliser la sauvegarde S3.") from exc
    return boto3.client(
        "s3",
        region_name=getattr(settings, "BACKUP_CLOUD_REGION", "us-east-1") or None,
        endpoint_url=getattr(settings, "BACKUP_CLOUD_ENDPOINT", "") or None,
        aws_access_key_id=settings.BACKUP_CLOUD_ACCESS_KEY,
        aws_secret_access_key=settings.BACKUP_CLOUD_SECRET_KEY,
    )


def _cloud_prefix() -> str:
    return (getattr(settings, "BACKUP_CLOUD_PREFIX", "ged") or "ged").strip("/")


def _taille_distante(client, key: str) -> int | None:
    try:
        return int(client.head_object(Bucket=settings.BACKUP_CLOUD_BUCKET, Key=key)["ContentLength"])
    except Exception:  # noqa: BLE001 — objet absent (404) ou inaccessible : on renverra
        return None


def _envoyer(client, path: Path, key: str) -> None:
    client.upload_file(str(path), settings.BACKUP_CLOUD_BUCKET, key)
    # Vérification après envoi : un « succès » sans relecture ne prouve rien.
    if _taille_distante(client, key) != path.stat().st_size:
        raise RuntimeError(f"Copie hors site non conforme pour {key} (taille relue différente).")


def _upload_cloud(root: Path) -> int:
    """Envoie la sauvegarde `root` hors site ; renvoie le nombre de fichiers
    transférés. Lève une exception au moindre écart : la sauvegarde est alors
    marquée en échec, retentée, et les notaires sont alertés."""
    client = _cloud_client()
    prefix = _cloud_prefix()
    envoyes = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relatif = path.relative_to(root).as_posix()
        if relatif in FICHIERS_INSTANTANE:
            continue
        key = f"{prefix}/{relatif}"
        if _taille_distante(client, key) == path.stat().st_size:
            continue  # déjà présente hors site (pièce immuable)
        _envoyer(client, path, key)
        envoyes += 1
    # L'instantané en DERNIER : un manifeste présent hors site garantit que
    # toutes les pièces qu'il référence y sont déjà.
    for nom in ("database.json.enc", "manifest.details.enc", "manifest.json"):
        if (root / nom).exists():
            _envoyer(client, root / nom, f"{prefix}/sauvegardes/{root.name}/{nom}")
            envoyes += 1
    return envoyes


def lister_sauvegardes_cloud() -> list[str]:
    client = _cloud_client()
    prefix = f"{_cloud_prefix()}/sauvegardes/"
    horodatages, jeton = set(), None
    while True:
        params = {"Bucket": settings.BACKUP_CLOUD_BUCKET, "Prefix": prefix}
        if jeton:
            params["ContinuationToken"] = jeton
        page = client.list_objects_v2(**params)
        for obj in page.get("Contents", []):
            reste = obj["Key"][len(prefix):]
            if reste.endswith("/manifest.json"):
                horodatages.add(reste.split("/")[0])
        if not page.get("IsTruncated"):
            break
        jeton = page.get("NextContinuationToken")
    return sorted(horodatages)


def telecharger_sauvegarde_cloud(horodatage: str, destination: Path) -> Path:
    """Reconstitue localement une sauvegarde hors site (après sinistre) :
    instantané + toutes les pièces référencées par son manifeste."""
    client = _cloud_client()
    prefix = _cloud_prefix()
    if horodatage == "derniere":
        disponibles = lister_sauvegardes_cloud()
        if not disponibles:
            raise RuntimeError("Aucune sauvegarde n'est présente hors site.")
        horodatage = disponibles[-1]
    racine = destination / horodatage
    racine.mkdir(parents=True, exist_ok=True)
    for nom in FICHIERS_INSTANTANE:
        client.download_file(settings.BACKUP_CLOUD_BUCKET, f"{prefix}/sauvegardes/{horodatage}/{nom}", str(racine / nom))
    manifest = json.loads((racine / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest.get("documents", []):
        if not item.get("storedPath"):
            continue
        cible = racine / item["storedPath"]
        cible.parent.mkdir(parents=True, exist_ok=True)
        client.download_file(settings.BACKUP_CLOUD_BUCKET, f"{prefix}/{item['storedPath']}", str(cible))
    return racine


FORMAT_INSTANTANE = "GED-DB-2"


def instantane_base() -> bytes:
    """Instantané logique de la base, en clair (chiffré ensuite).

    Le journal d'audit est exporté À PART, à la microseconde près : l'export
    JSON de Django tronque les horodatages à la milliseconde, or l'empreinte
    de chaque entrée est calculée sur l'horodatage complet. Restauré par
    `dumpdata`, le journal aurait vu sa chaîne déclarée rompue — il aurait
    perdu toute valeur probante précisément le jour d'un sinistre."""
    from audit.models import AuditLog
    tampon = io.StringIO()
    call_command("dumpdata", exclude=[*TABLES_EXCLUES, "audit.auditlog"], stdout=tampon)
    journal = [
        {"id": e.pk, "user_id": e.user_id, "action": e.action, "target_type": e.target_type, "target_id": e.target_id,
         "timestamp": e.timestamp.isoformat(), "ip_address": e.ip_address, "result": e.result, "metadata": e.metadata,
         "previous_hash": e.previous_hash, "entry_hash": e.entry_hash}
        for e in AuditLog.objects.order_by("id").iterator()
    ]
    return json.dumps({"format": FORMAT_INSTANTANE, "objects": json.loads(tampon.getvalue()), "audit": journal},
                      ensure_ascii=False).encode("utf-8")


def run_backup(request_user):
    documents = list(Document.objects.exclude(statut=Document.Status.DESTRUCTION_AUTHORIZED).exclude(statut=getattr(Document.Status, "DESTROYED", "__never__")).select_related("dossier"))
    timestamp = timezone.now().strftime("%Y%m%d-%H%M%S")
    root = _local_root() / timestamp
    # Deux sauvegardes dans la même seconde (manuelle + planifiée) partageaient
    # le même répertoire : la seconde écrasait la première.
    suffixe = 1
    while root.exists():
        suffixe += 1
        root = _local_root() / f"{timestamp}-{suffixe}"
    root.mkdir(parents=True)
    try:
        count, total = _copy_documents(documents, root)
        # Logical database snapshot contains accounts, rights, metadata and audit logs.
        clair = instantane_base()
        chiffre, db_key_id = encrypt(clair)
        db_path = root / "database.json.enc"
        db_path.write_bytes(chiffre)
        manifest = _manifest(documents)
        manifest["database"] = {
            "format": FORMAT_INSTANTANE,
            "file": "database.json.enc",
            "encrypted": True,
            "encryptionKeyId": db_key_id,
            # Empreinte du CHIFFRÉ : elle se vérifie sans posséder la clé,
            # ce dont le test de restauration a besoin.
            "sha256": hashlib.sha256(chiffre).hexdigest(),
            "plaintextSha256": hashlib.sha256(clair).hexdigest(),
            "sizeBytes": db_path.stat().st_size,
            "excludedTables": TABLES_EXCLUES,
        }
        details_clair = json.dumps(_details(documents), ensure_ascii=False).encode("utf-8")
        details_chiffre, details_key_id = encrypt(details_clair)
        (root / "manifest.details.enc").write_bytes(details_chiffre)
        manifest["details"] = {
            "file": "manifest.details.enc", "encrypted": True, "encryptionKeyId": details_key_id,
            "sha256": hashlib.sha256(details_chiffre).hexdigest(),
            "plaintextSha256": hashlib.sha256(details_clair).hexdigest(),
        }
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


# --------------------------------------------------------------------------
# Résolution des chemins, rotation, exercice de restauration, fraîcheur
# --------------------------------------------------------------------------

def backup_root_of(run) -> Path:
    racine = Path(run.local_path)
    return racine if racine.is_absolute() else Path(settings.BASE_DIR) / racine


def _inside_backup_root(path: Path) -> bool:
    """Garde-fou de la rotation : on ne supprime JAMAIS hors du dépôt de
    sauvegardes, quelle que soit la valeur enregistrée en base."""
    racine = _local_root().resolve()
    try:
        path.resolve().relative_to(racine)
    except ValueError:
        return False
    return path.resolve() != racine


def _garder(runs, now) -> set[int]:
    """Politique « grand-père / père / fils » : toutes les sauvegardes des
    dernières 24 h, puis la plus récente de chaque jour, semaine et mois."""
    quotidien = getattr(settings, "BACKUP_RETENTION_DAILY", 7)
    hebdo = getattr(settings, "BACKUP_RETENTION_WEEKLY", 4)
    mensuel = getattr(settings, "BACKUP_RETENTION_MONTHLY", 12)
    garde, jours, semaines, mois = set(), set(), set(), set()
    for run in runs:  # du plus récent au plus ancien
        local = timezone.localtime(run.created_at)
        if now - run.created_at <= timedelta(hours=24):
            garde.add(run.pk)
        jour, semaine, m = local.date(), tuple(local.isocalendar())[:2], (local.year, local.month)
        if jour not in jours and len(jours) < quotidien:
            jours.add(jour)
            garde.add(run.pk)
        if semaine not in semaines and len(semaines) < hebdo:
            semaines.add(semaine)
            garde.add(run.pk)
        if m not in mois and len(mois) < mensuel:
            mois.add(m)
            garde.add(run.pk)
    return garde


def prune_backups(now=None) -> dict:
    """Supprime les répertoires de sauvegarde hors politique de rétention.

    Ne supprime jamais : la sauvegarde réussie la plus récente, la dernière
    sauvegarde vérifiée par un exercice PRA, ni quoi que ce soit hors du
    dépôt de sauvegardes."""
    now = now or timezone.now()
    reussies = list(BackupRun.objects.filter(kind="backup", status=BackupRun.Status.SUCCESS, pruned_at__isnull=True)
                    .exclude(local_path="").order_by("-created_at"))
    garde = _garder(reussies, now)
    if reussies:
        garde.add(reussies[0].pk)
    verifiee = BackupRun.objects.filter(kind="restore_drill", status=BackupRun.Status.SUCCESS).exclude(local_path="").order_by("-created_at").first()
    if verifiee:
        garde.update(r.pk for r in reussies if r.local_path == verifiee.local_path)
    candidates = [r for r in reussies if r.pk not in garde]
    # Répertoires partiels laissés par une sauvegarde échouée depuis > 2 jours.
    candidates += list(BackupRun.objects.filter(kind="backup", status=BackupRun.Status.FAILURE, pruned_at__isnull=True,
                                                created_at__lt=now - timedelta(days=2)).exclude(local_path=""))
    supprimees, octets = 0, 0
    for run in candidates:
        racine = backup_root_of(run)
        if racine.exists():
            if not _inside_backup_root(racine):
                continue
            octets += sum(f.stat().st_size for f in racine.rglob("*") if f.is_file())
            shutil.rmtree(racine)
        run.pruned_at = now
        run.save(update_fields=["pruned_at"])
        supprimees += 1
    return {"items": supprimees, "message": f"{supprimees} sauvegarde(s) retirée(s) par la rotation "
                                            f"({octets // (1024 * 1024)} Mo libérés), {len(garde)} conservée(s)."}


def verifier_sauvegarde(root: Path) -> dict:
    """Vérifie une sauvegarde sans rien modifier ; lève ValueError au moindre
    écart. Renvoie le manifeste, l'instantané déchiffré et le décompte.

    Déchiffrer prouve que la clé de l'étude ouvre bien cette sauvegarde : sans
    ce contrôle, un instantané illisible passerait pour restaurable le jour où
    on en a réellement besoin."""
    from documents.crypto import decrypt
    chemin_manifeste = root / "manifest.json"
    if not chemin_manifeste.exists():
        raise ValueError(f"Manifeste absent : {chemin_manifeste}")
    manifest = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
    entree = manifest["database"]
    chemin_db = root / entree.get("file", "database.json")
    if not chemin_db.exists():
        raise ValueError("Instantané de la base absent de la sauvegarde.")
    db = chemin_db.read_bytes()
    if hashlib.sha256(db).hexdigest() != entree["sha256"]:
        raise ValueError("Base de sauvegarde corrompue.")
    clair = db
    if entree.get("encrypted"):
        try:
            clair = decrypt(db, entree.get("encryptionKeyId") or None)
        except Exception as exc:
            raise ValueError(f"Instantané indéchiffrable avec le trousseau actuel (clé « {entree.get('encryptionKeyId')} ») : "
                             "restaurez d'abord les clés depuis le paquet de récupération.") from exc
        attendu = entree.get("plaintextSha256")
        if attendu and hashlib.sha256(clair).hexdigest() != attendu:
            raise ValueError("Contenu déchiffré non conforme au manifeste.")
    json.loads(clair.decode("utf-8"))
    details = manifest.get("details")
    if details:
        blob = (root / details["file"]).read_bytes()
        if hashlib.sha256(blob).hexdigest() != details["sha256"]:
            raise ValueError("Métadonnées détaillées corrompues.")
        if hashlib.sha256(decrypt(blob, details.get("encryptionKeyId") or None)).hexdigest() != details["plaintextSha256"]:
            raise ValueError("Métadonnées détaillées non conformes au manifeste.")
    restored = expected = 0
    for item in manifest.get("documents", []):
        if not item.get("storedPath"):
            continue
        expected += 1
        p = root / item["storedPath"]
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == item["encryptedSha256"]:
            restored += 1
    if restored != expected:
        raise ValueError(f"Documents vérifiés : {restored}/{expected}.")
    return {"manifest": manifest, "database": clair, "documents": restored}


class RestaurationRefusee(RuntimeError):
    pass


def restaurer_sauvegarde(root: Path) -> dict:
    """Restaure une sauvegarde dans une base NEUVE (après `migrate`).

    Ordre : vérification complète → chargement de l'instantané (transaction
    unique, contraintes vérifiées) → réinstallation des pièces chiffrées →
    contrôle de chaque pièce (déchiffrement + SHA-256) et de la chaîne d'audit.
    Refuse d'écraser une base qui contient déjà des données : une restauration
    ne remplace jamais une production vivante."""
    from django.core import serializers
    from django.core.files.storage import default_storage
    from django.db import connection, transaction

    from accounts.models import User
    from audit.models import AuditLog
    from audit.services import verify_chain
    from dossiers.models import Dossier

    if User.objects.exists() or Dossier.objects.exists() or Document.objects.exists() or AuditLog.objects.exists():
        raise RestaurationRefusee(
            "La base cible contient déjà des données : restauration refusée. Restaurez dans une base neuve "
            "(nouveau serveur ou volume PostgreSQL vide), après `python manage.py migrate`.")
    verification = verifier_sauvegarde(root)
    contenu = json.loads(verification["database"].decode("utf-8"))
    # Format historique : simple liste `dumpdata` (journal inclus, horodatages
    # tronqués). Format actuel : objets + journal d'audit exact.
    objets_bruts, journal = (contenu, None) if isinstance(contenu, list) else (contenu["objects"], contenu.get("audit"))
    objets = 0
    with transaction.atomic():
        for objet in serializers.deserialize("python", objets_bruts, ignorenonexistent=True):
            # Sauvegarde « brute » : pas de logique métier, les identifiants et
            # horodatages d'origine sont conservés.
            objet.save()
            objets += 1
        if journal:
            from datetime import datetime
            AuditLog.objects.bulk_create([
                AuditLog(id=e["id"], user_id=e["user_id"], action=e["action"], target_type=e["target_type"],
                         target_id=e["target_id"], timestamp=datetime.fromisoformat(e["timestamp"]), ip_address=e["ip_address"],
                         result=e["result"], metadata=e["metadata"], previous_hash=e["previous_hash"], entry_hash=e["entry_hash"])
                for e in journal
            ], batch_size=500)
            objets += len(journal)
        connection.check_constraints()
    # Les compteurs d'identifiants (PostgreSQL) doivent repartir après le
    # plus grand identifiant restauré, sinon le prochain dépôt échouerait.
    from django.apps import apps
    from django.core.management.color import no_style
    requetes = connection.ops.sequence_reset_sql(no_style(), apps.get_models())
    if requetes:
        with connection.cursor() as cursor:
            for requete in requetes:
                cursor.execute(requete)
    installes = deja = 0
    for item in verification["manifest"].get("documents", []):
        chemin = item.get("storedPath")
        if not chemin:
            continue
        if default_storage.exists(chemin):
            deja += 1
            continue
        with (root / chemin).open("rb") as source:
            nom = default_storage.save(chemin, source)
        if nom != chemin:
            raise RestaurationRefusee(f"Emplacement déjà occupé pour {chemin} : restauration interrompue.")
        installes += 1
    illisibles = []
    for doc in Document.objects.exclude(fichier="").exclude(statut=Document.Status.DESTROYED).iterator():
        try:
            if hashlib.sha256(doc.decrypted_bytes()).hexdigest() != doc.sha256:
                illisibles.append(doc.reference)
        except Exception:  # noqa: BLE001 — fichier absent ou clé manquante
            illisibles.append(doc.reference)
    chaine = verify_chain()
    return {"objets": objets, "pieces_installees": installes, "pieces_deja_presentes": deja,
            "pieces_illisibles": illisibles, "audit": chaine}


def restore_drill(request_user=None) -> BackupRun:
    """Test de restauration NON destructif de la dernière sauvegarde réussie :
    empreintes de la base et de chaque binaire, déchiffrement de la base et des
    métadonnées avec la clé de l'étude. Le résultat est toujours enregistré."""
    from documents.crypto import decrypt
    run = BackupRun.objects.filter(kind="backup", status=BackupRun.Status.SUCCESS, pruned_at__isnull=True).order_by("-created_at").first()
    if not run:
        return BackupRun.objects.create(status=BackupRun.Status.FAILURE, kind="restore_drill", requested_by=request_user,
                                        message="Exercice PRA impossible : aucune sauvegarde complète réussie.")
    root = backup_root_of(run)
    restored = 0
    try:
        verification = verifier_sauvegarde(root)
        restored = verification["documents"]
    except Exception as exc:  # noqa: BLE001 — tout écart est un échec de l'exercice
        return BackupRun.objects.create(
            status=BackupRun.Status.FAILURE, kind="restore_drill", requested_by=request_user,
            checked_documents=restored, local_path=run.local_path, cloud_status=run.cloud_status,
            message=f"Exercice PRA échoué sur la sauvegarde du {timezone.localtime(run.created_at):%d/%m/%Y %H:%M} : {exc}")
    return BackupRun.objects.create(
        status=BackupRun.Status.SUCCESS, kind="restore_drill", requested_by=request_user, checked_documents=restored,
        local_path=run.local_path, cloud_status=run.cloud_status,
        message=f"Test PRA réussi : base vérifiée, {restored} document(s) vérifié(s), aucune modification de production "
                f"(sauvegarde du {timezone.localtime(run.created_at):%d/%m/%Y %H:%M}).")


def backup_max_age() -> timedelta:
    heures = getattr(settings, "BACKUP_MAX_AGE_HOURS", 0) or 0
    if heures:
        return timedelta(hours=heures)
    return timedelta(seconds=2 * getattr(settings, "BACKUP_INTERVAL_SECONDS", 21600))


def backup_freshness(now=None) -> dict:
    now = now or timezone.now()
    derniere = BackupRun.objects.filter(kind="backup", status=BackupRun.Status.SUCCESS).order_by("-created_at").first()
    limite = backup_max_age()
    if derniere is None:
        return {"ok": not Document.objects.exists(), "age": None, "limit": limite, "last": None}
    age = now - derniere.created_at
    return {"ok": age <= limite, "age": age, "limit": limite, "last": derniere}
