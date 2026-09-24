"""Restaure la GED à partir d'une sauvegarde, dans une base NEUVE.

    # Sauvegarde locale (volume backup_store) :
    python manage.py restaurer_sauvegarde --dossier backup_store/20260923-020000

    # Après la perte du serveur : depuis le stockage hors site
    python manage.py restaurer_sauvegarde --cloud liste      # horodatages disponibles
    python manage.py restaurer_sauvegarde --cloud derniere   # la plus récente

Prérequis (voir DEPLOIEMENT_SERVEUR.md, « Restaurer après un sinistre ») :
1. les CLÉS de chiffrement restaurées depuis le paquet de récupération ;
2. une base vide migrée : `python manage.py migrate`.

La commande vérifie tout avant d'écrire, refuse une base non vide, puis
contrôle chaque pièce restaurée et la chaîne d'audit.
"""
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from audit.services import log_system_event
from settings_app.backup_service import (RestaurationRefusee, lister_sauvegardes_cloud, restaurer_sauvegarde,
                                         telecharger_sauvegarde_cloud)


class Command(BaseCommand):
    help = "Restaure une sauvegarde complète (base + pièces) dans une base neuve."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--dossier", help="Répertoire d'une sauvegarde locale (contenant manifest.json).")
        source.add_argument("--cloud", help="Horodatage d'une sauvegarde hors site, « derniere » ou « liste ».")

    def handle(self, *args, **options):
        if options["cloud"] == "liste":
            for horodatage in lister_sauvegardes_cloud():
                self.stdout.write(horodatage)
            return
        if options["dossier"]:
            racine = Path(options["dossier"])
            if not racine.is_absolute():
                racine = Path(settings.BASE_DIR) / racine
            return self._restaurer(racine)
        with tempfile.TemporaryDirectory(prefix="ged-restauration-") as tmp:
            self.stdout.write("Téléchargement de la sauvegarde hors site…")
            racine = telecharger_sauvegarde_cloud(options["cloud"], Path(tmp))
            self._restaurer(racine)

    def _restaurer(self, racine: Path):
        self.stdout.write(f"Vérification puis restauration de {racine.name}…")
        try:
            bilan = restaurer_sauvegarde(racine)
        except (RestaurationRefusee, ValueError) as exc:
            raise CommandError(str(exc))
        ok = not bilan["pieces_illisibles"] and bilan["audit"]["ok"]
        log_system_event("backup_restored", "backup", racine.name, result="success" if ok else "failure",
                         metadata={"objets": bilan["objets"], "pieces": bilan["pieces_installees"],
                                   "illisibles": len(bilan["pieces_illisibles"]), "audit_ok": bilan["audit"]["ok"]})
        self.stdout.write(f"  enregistrements restaurés : {bilan['objets']}")
        self.stdout.write(f"  pièces réinstallées : {bilan['pieces_installees']} (déjà présentes : {bilan['pieces_deja_presentes']})")
        self.stdout.write(f"  chaîne d'audit : {'intègre' if bilan['audit']['ok'] else 'ROMPUE'} ({bilan['audit']['checked']} entrées)")
        if not ok:
            raise CommandError(f"Restauration INCOMPLÈTE : {len(bilan['pieces_illisibles'])} pièce(s) illisible(s) "
                               f"({', '.join(bilan['pieces_illisibles'][:10])}). Vérifiez les clés restaurées.")
        self.stdout.write(self.style.SUCCESS("Restauration terminée et vérifiée. Redémarrez les services puis contrôlez "
                                             "l'écran Sauvegarde · Supervision."))
