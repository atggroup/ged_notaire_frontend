"""Émet les rappels d'échéance des tâches (J-7, J-3, J-1, jour J), les
alertes de retard et les escalades.

La logique vit dans `notifications.tasks` et tourne normalement dans
l'exécutant (`python manage.py run_worker`, travail `rappels_taches`). Cette
commande reste utile pour un passage ponctuel :

    python manage.py envoyer_rappels [--email] [--dry-run]

Elle est idempotente : chaque palier est revendiqué une seule fois.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from notifications.services import send_pending_emails
from notifications.tasks import envoyer_rappels_taches


class Command(BaseCommand):
    help = "Envoie les rappels de tâches arrivés à échéance et signale les retards."

    def add_arguments(self, parser):
        parser.add_argument("--email", action="store_true",
                            help="Envoie aussitôt la file des e-mails (sinon : travail `envoi_emails`).")
        parser.add_argument("--dry-run", action="store_true",
                            help="Affiche ce qui serait envoyé sans rien écrire.")

    def handle(self, *args, **options):
        if options["dry_run"]:
            with transaction.atomic():
                resultat = envoyer_rappels_taches()
                transaction.set_rollback(True)
            self.stdout.write(self.style.SUCCESS(f"{resultat['message']} (simulation)"))
            return
        resultat = envoyer_rappels_taches()
        self.stdout.write(self.style.SUCCESS(resultat["message"]))
        if options["email"]:
            self.stdout.write(send_pending_emails()["message"])
