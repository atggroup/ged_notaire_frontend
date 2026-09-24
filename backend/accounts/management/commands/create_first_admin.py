"""Create the one allowed invitation-free Notaire account."""
import os
import secrets

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import User


class Command(BaseCommand):
    help = "Creates the first Notaire/Admin account. May only be used on an empty user table."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Adresse du notaire (sinon INITIAL_ADMIN_EMAIL).")
        parser.add_argument("--prenom", help="Prénom ou titre affiché avant le nom (sinon INITIAL_ADMIN_PRENOM).")
        parser.add_argument("--nom", help="Nom affiché du notaire (sinon INITIAL_ADMIN_NOM).")
        parser.add_argument("--cabinet", help="Nom du cabinet (sinon CABINET_NAME).")

    def handle(self, *args, **options):
        if User.objects.exists():
            raise CommandError("Un compte existe déjà : utilisez les invitations administratives.")
        email = (options.get("email") or os.getenv("INITIAL_ADMIN_EMAIL", "")).lower()
        if not email or email.endswith("@example.com"):
            raise CommandError("INITIAL_ADMIN_EMAIL doit contenir l'adresse réelle du notaire.")
        fourni = os.getenv("INITIAL_ADMIN_PASSWORD") or ""
        password = fourni or secrets.token_urlsafe(18)
        # Même politique que pour tout compte de l'étude : un mot de passe
        # d'amorçage faible ouvrirait le compte le plus privilégié.
        try:
            validate_password(password, User(email=email))
        except ValidationError as exc:
            raise CommandError("INITIAL_ADMIN_PASSWORD refusé : " + " ".join(exc.messages))
        # Rôle notaire, mais PAS superutilisateur Django : l'admin Django
        # contourne le second facteur et le journal d'audit de la GED.
        # Nom réel du notaire dès la création : sans lui, tout l'applicatif
        # (en-tête, journal d'audit, e-mails) affichait « Notaire Administrateur ».
        prenom = (options.get("prenom") or os.getenv("INITIAL_ADMIN_PRENOM", "")).strip() or "Notaire"
        nom = (options.get("nom") or os.getenv("INITIAL_ADMIN_NOM", "")).strip() or "Administrateur"
        user = User.objects.create_user(email=email, password=password, role=User.Role.ADMIN,
                                        first_name=prenom[:150], last_name=nom[:150])
        self.stdout.write(self.style.SUCCESS(f"Compte notaire créé : {user.email} ({user.display_name})"))
        cabinet = (options.get("cabinet") or os.getenv("CABINET_NAME", "")).strip()
        if cabinet:
            from settings_app.models import CabinetSettings
            reglages, _ = CabinetSettings.objects.get_or_create(pk=1)
            reglages.cabinet_name = cabinet[:255]
            reglages.save(update_fields=["cabinet_name", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"Nom du cabinet : {reglages.cabinet_name}"))
        if fourni:
            self.stdout.write(self.style.WARNING(
                "Retirez maintenant INITIAL_ADMIN_PASSWORD de .env.prod et mettez CREATE_INITIAL_ADMIN=false."))
        else:
            self.stdout.write(self.style.WARNING(f"Mot de passe à conserver maintenant (ne sera plus affiché) : {password}"))
