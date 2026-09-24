"""Sauvegarde complète ponctuelle (documents chiffrés + base + manifestes).

La sauvegarde régulière est assurée par l'exécutant (`run_worker --groupe
sauvegarde`), qui ajoute la rotation, le contrôle de fraîcheur et l'alerte en
cas d'échec. Code de sortie non nul en cas d'échec (Planificateur Windows).
"""
from django.core.management.base import BaseCommand, CommandError

from audit.services import log_system_event
from settings_app.backup_service import run_backup


class Command(BaseCommand):
    help = "Crée une sauvegarde complète GED (documents chiffrés + base + manifeste)."

    def handle(self, *args, **options):
        try:
            run = run_backup(None)
        except Exception as exc:
            log_system_event("backup_run_failed", "backup", "commande", result="failure", metadata={"error": str(exc)[:500]})
            raise CommandError(str(exc))
        log_system_event("backup_run_completed", "backup", str(run.pk), metadata={"documents": run.checked_documents, "cloud": run.cloud_status})
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde {run.pk} réussie : {run.local_path}"))
