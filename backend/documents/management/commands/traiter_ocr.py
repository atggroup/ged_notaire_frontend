"""Extrait le texte des pièces en attente, hors du chemin de la requête HTTP.

L'OCR tournait auparavant à l'intérieur de la requête de dépôt. Rasteriser
50 pages à 300 dpi occupe un fil d'exécution plusieurs minutes ; gunicorn n'en
offre que six et nginx coupe à 180 s.

La file est normalement traitée par l'exécutant (`run_worker`, travail
`ocr`), avec reprise des pièces bloquées et nouvelles tentatives. Cette
commande permet un passage ponctuel :

    python manage.py traiter_ocr [--lot 5]
"""
from django.core.management.base import BaseCommand

from documents.tasks import traiter_file_ocr


class Command(BaseCommand):
    help = "Traite les documents dont l'extraction de texte est en attente."

    def add_arguments(self, parser):
        parser.add_argument("--lot", type=int, default=5,
                            help="Nombre de pièces traitées par passage (défaut : 5).")

    def handle(self, *args, **options):
        resultat = traiter_file_ocr(lot=max(1, options["lot"]))
        self.stdout.write(self.style.SUCCESS(resultat["message"]))
