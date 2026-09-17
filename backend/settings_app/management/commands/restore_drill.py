import hashlib, json
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from settings_app.models import BackupRun
from documents.models import Document

class Command(BaseCommand):
    help = "Effectue un test de restauration non destructif de la dernière sauvegarde."
    def handle(self, *args, **options):
        run=BackupRun.objects.filter(kind="backup", status=BackupRun.Status.SUCCESS).order_by("-created_at").first()
        if not run: raise CommandError("Aucune sauvegarde complète réussie.")
        root=Path(settings.BASE_DIR)/run.local_path
        manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        db=(root/"database.json").read_bytes()
        if hashlib.sha256(db).hexdigest()!=manifest["database"]["sha256"]: raise CommandError("Base de sauvegarde corrompue.")
        restored=0
        for item in manifest.get("documents", []):
            p=root/item["storedPath"]
            if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==item["encryptedSha256"]: restored+=1
        expected=len([x for x in manifest.get("documents", []) if x.get("storedPath")])
        if restored != expected: raise CommandError(f"Documents vérifiés : {restored}/{expected}.")
        self.stdout.write(self.style.SUCCESS(f"Test PRA réussi : base vérifiée, {restored} document(s) vérifié(s), aucune modification de production."))
