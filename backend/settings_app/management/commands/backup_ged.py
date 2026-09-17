from django.core.management.base import BaseCommand, CommandError
from settings_app.backup_service import run_backup

class Command(BaseCommand):
    help = "Crée une sauvegarde complète GED (documents chiffrés + base + manifeste)."
    def handle(self, *args, **options):
        try:
            run = run_backup(None)
        except Exception as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde {run.pk} réussie : {run.local_path}"))
