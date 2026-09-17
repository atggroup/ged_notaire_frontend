"""Create the one allowed invitation-free Notaire account."""
import os
import secrets
from django.core.management.base import BaseCommand, CommandError
from accounts.models import User


class Command(BaseCommand):
    help = "Creates the first Notaire/Admin account. May only be used on an empty user table."

    def handle(self, *args, **options):
        if User.objects.exists():
            raise CommandError("Un compte existe déjà : utilisez les invitations administratives.")
        email = os.getenv("INITIAL_ADMIN_EMAIL", "notaire@example.com").lower()
        password = os.getenv("INITIAL_ADMIN_PASSWORD") or secrets.token_urlsafe(18)
        user = User.objects.create_superuser(email=email, password=password, first_name="Notaire", last_name="Administrateur")
        self.stdout.write(self.style.SUCCESS(f"Compte admin créé : {user.email}"))
        self.stdout.write(self.style.WARNING(f"Mot de passe à conserver maintenant (ne sera plus affiché) : {password}"))
