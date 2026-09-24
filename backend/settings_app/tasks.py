"""Sauvegarde et PRA automatiques (groupe « sauvegarde », exécutant dédié).

    sauvegarde (toutes les 6 h par défaut) ─► rotation ─► BackupRun + audit
          └─ échec ─► nouvelle tentative (30 min) ─► alerte CRITIQUE aux notaires
    fraîcheur (chaque heure) ─► aucune sauvegarde réussie récente ─► alerte
    exercice PRA (hebdomadaire) ─► vérification non destructive ─► rapport
"""
from django.conf import settings
from django.utils import timezone

from core.jobs import job


def _admins():
    from notifications.services import active_admins
    return active_admins()


@job("sauvegarde", "Sauvegarde complète chiffrée", group="sauvegarde",
     every=getattr(settings, "BACKUP_INTERVAL_SECONDS", 21600), retries=2, retry_delay=1800, lock_ttl=4 * 3600)
def sauvegarde():
    from .backup_service import prune_backups, run_backup
    run = run_backup(None)  # lève en cas d'échec : l'exécutant alerte et retente
    rotation = prune_backups()
    return {"items": run.checked_documents,
            "message": f"{run.message} Rotation : {rotation['message']}"}


@job("fraicheur_sauvegarde", "Contrôle de fraîcheur des sauvegardes", group="sauvegarde", every=3600, lock_ttl=600)
def fraicheur_sauvegarde():
    from notifications.models import Notification
    from notifications.services import emit
    from .backup_service import backup_freshness
    etat = backup_freshness()
    if etat["ok"]:
        return {"items": 0, "message": "Dernière sauvegarde réussie dans le délai toléré."}
    if etat["last"] is None:
        detail = "Aucune sauvegarde complète réussie n'existe alors que la GED contient des documents."
    else:
        heures = int(etat["age"].total_seconds() // 3600)
        detail = (f"La dernière sauvegarde réussie date de {heures} h "
                  f"(le {timezone.localtime(etat['last'].created_at):%d/%m/%Y à %H:%M}) ; "
                  f"délai toléré : {int(etat['limit'].total_seconds() // 3600)} h.")
    emit(_admins(), "backup_stale", "Sauvegarde en retard",
         f"{detail} Vérifiez l'exécutant de sauvegarde (écran Sauvegarde · Supervision).",
         severity=Notification.Severity.CRITICAL, target_type="backup", target_id="fraicheur",
         event_key=f"backup-stale:{timezone.localdate().isoformat()}")
    from audit.services import log_system_event
    log_system_event("backup_stale_alert", "backup", "fraicheur", result="failure", metadata={"detail": detail})
    return {"items": 1, "message": detail}


def cles_non_sequestrees() -> list[str]:
    """Clés du trousseau jamais exportées dans un paquet de récupération.

    Sans ce paquet conservé HORS du serveur, la perte de la machine rend
    toutes les sauvegardes illisibles : elles sont chiffrées avec ces clés."""
    from audit.models import AuditLog
    from documents.crypto import _keyring
    exportees = set()
    for metadata in AuditLog.objects.filter(action="encryption_keys_recovery_exported").values_list("metadata", flat=True):
        exportees.update((metadata or {}).get("keyIds", []))
    return sorted(set(_keyring()) - exportees)


@job("sequestre_cles", "Contrôle du séquestre des clés de chiffrement", group="sauvegarde", daily_at="08:00", lock_ttl=600)
def sequestre_cles():
    from notifications.models import Notification
    from notifications.services import emit
    manquantes = cles_non_sequestrees()
    if not manquantes:
        return {"items": 0, "message": "Toutes les clés en service figurent dans un paquet de récupération exporté."}
    message = (f"{len(manquantes)} clé(s) de chiffrement ({', '.join(manquantes)}) n'ont jamais été exportées dans un "
               "paquet de récupération. Si ce serveur est perdu, les documents ET les sauvegardes seront illisibles. "
               "Configuration → Clés → « Exporter le paquet de récupération », puis conservez-le hors du serveur "
               "(coffre, clé USB chiffrée au coffre de l'étude).")
    emit(_admins(), "key_escrow_missing", "Clés de chiffrement non sauvegardées", message,
         severity=Notification.Severity.CRITICAL, target_type="backup", target_id="sequestre",
         event_key=f"key-escrow:{timezone.localdate().isoformat()}")
    return {"items": len(manquantes), "message": message}


@job("exercice_pra", "Exercice PRA : test de restauration de la dernière sauvegarde", group="sauvegarde",
     daily_at="04:00", weekday=getattr(settings, "RESTORE_DRILL_WEEKDAY", 6), retries=1, retry_delay=3600, lock_ttl=4 * 3600)
def exercice_pra():
    from notifications.models import Notification
    from notifications.services import emit
    from .backup_service import restore_drill
    from .models import BackupRun
    run = restore_drill(None)
    if run.status != BackupRun.Status.SUCCESS:
        raise RuntimeError(run.message)  # alerte et nouvelle tentative par l'exécutant
    emit(_admins(), "restore_drill", "Exercice PRA réussi", run.message,
         severity=Notification.Severity.INFO, target_type="backup", target_id=str(run.pk),
         event_key=f"restore-drill-ok:{run.pk}")
    return {"items": run.checked_documents, "message": run.message}
