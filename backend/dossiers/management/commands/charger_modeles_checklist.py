"""Charge les modèles de checklist proposés (`dossiers/modeles_checklist.json`).

    python manage.py charger_modeles_checklist            # ajoute ce qui manque
    python manage.py charger_modeles_checklist --inactifs # ajoute, désactivés

Idempotent : un modèle existant (même domaine, même libellé) n'est jamais
écrasé — les ajustements du notaire sont préservés. Ces listes sont une
PROPOSITION : c'est au notaire de décider des pièces exigées.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from dossiers.models import ChecklistTemplate
from ged_backend.referentiels import DOMAINE_LABELS, TYPE_DOCUMENT_LABELS


class Command(BaseCommand):
    help = "Charge les modèles de checklist proposés par domaine (sans écraser l'existant)."

    def add_arguments(self, parser):
        parser.add_argument("--inactifs", action="store_true",
                            help="Crée les modèles désactivés, à activer après relecture par le notaire.")

    def handle(self, *args, **options):
        source = Path(__file__).resolve().parents[2] / "modeles_checklist.json"
        modeles = json.loads(source.read_text(encoding="utf-8"))["modeles"]
        crees = 0
        for domaine, lignes in modeles.items():
            if domaine not in DOMAINE_LABELS:
                self.stderr.write(f"Domaine inconnu ignoré : {domaine}")
                continue
            for ordre, (label, type_code, requis) in enumerate(lignes):
                if type_code and type_code not in TYPE_DOCUMENT_LABELS:
                    self.stderr.write(f"Type inconnu ignoré : {type_code} ({label})")
                    type_code = ""
                _, cree = ChecklistTemplate.objects.get_or_create(
                    domaine=domaine, label=label,
                    defaults={"type_code": type_code, "required": requis, "order": ordre, "active": not options["inactifs"]})
                crees += int(cree)
        self.stdout.write(self.style.SUCCESS(
            f"{crees} modèle(s) ajouté(s), {ChecklistTemplate.objects.count()} au total. "
            "Rappel : ces listes sont une proposition à valider par le notaire."))
