"""Test de restauration non destructif de la dernière sauvegarde.

Planifié chaque semaine par l'exécutant (travail `exercice_pra`) ; peut être
lancé à la main. Le résultat est enregistré (`BackupRun`, type
`restore_drill`) et journalisé : un exercice PRA n'est plus une ligne de
console que personne ne relit.
"""
from django.core.management.base import BaseCommand, CommandError

from audit.services import log_system_event
from settings_app.backup_service import restore_drill
from settings_app.models import BackupRun


class Command(BaseCommand):
    help = "Effectue un test de restauration non destructif de la dernière sauvegarde."

    def handle(self, *args, **options):
        run = restore_drill(None)
        echec = run.status != BackupRun.Status.SUCCESS
        log_system_event("backup_restore_drill", "backup", str(run.pk), result="failure" if echec else "success",
                         metadata={"message": run.message[:300], "documents": run.checked_documents})
        if echec:
            raise CommandError(run.message)
        self.stdout.write(self.style.SUCCESS(run.message))
