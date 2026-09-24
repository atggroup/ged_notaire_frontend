"""Prépare un fichier de configuration de production avec des secrets NEUFS.

    python manage.py preparer_secrets_production
        → écrit backend/.env.prod.nouveau (ne remplace jamais .env.prod)

Pourquoi : les secrets de production étaient identiques à ceux du
développement et circulaient en clair avec le projet. La commande :

- génère un nouveau SECRET_KEY, un nouveau mot de passe PostgreSQL et une
  nouvelle clé de chiffrement documentaire (activée) ;
- conserve l'ANCIENNE clé documentaire dans le trousseau sous l'identifiant
  « legacy », le temps de rechiffrer les pièces (`rechiffrer_documents`) ;
- reprend les réglages non secrets du fichier actuel ;
- marque « A_REMPLACER » tout ce qui dépend de vous (domaine, mots de passe
  Gmail et Google à régénérer chez les fournisseurs, stockage hors site).
  La production refuse de démarrer tant qu'il reste un « A_REMPLACER ».

Aucun secret n'est affiché à l'écran.
"""
import base64
import json
import os
import secrets
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

A_REMPLACER = "A_REMPLACER"
# Réglages non secrets repris tels quels depuis le fichier actuel.
REPRIS = ["POSTGRES_DB", "POSTGRES_USER", "EMAIL_BACKEND", "EMAIL_HOST", "EMAIL_PORT", "EMAIL_HOST_USER", "EMAIL_USE_TLS",
          "EMAIL_USE_SSL", "DEFAULT_FROM_EMAIL", "GOOGLE_OAUTH_CLIENT_ID", "FRONTEND_OAUTH_LANDING_PATH", "OCR_LANG", "OCR_DPI",
          "OCR_MAX_PAGES", "DOCUMENT_MAX_UPLOAD_BYTES", "WEB_CONCURRENCY", "GUNICORN_THREADS", "GUNICORN_TIMEOUT",
          "BACKUP_INTERVAL_SECONDS", "NUM_PROXIES", "TRUSTED_CLIENT_IP_HEADER"]


def lire_env(chemin: Path) -> dict[str, str]:
    valeurs = {}
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, valeur = ligne.split("=", 1)
        valeurs[cle.strip()] = valeur.strip().strip('"').strip("'")
    return valeurs


def ancien_trousseau(actuel: dict[str, str]) -> dict[str, str]:
    """Toutes les clés qui chiffrent aujourd'hui des pièces ou des sauvegardes."""
    trousseau = {}
    if actuel.get("DOCUMENT_ENCRYPTION_KEYS"):
        try:
            trousseau.update(json.loads(actuel["DOCUMENT_ENCRYPTION_KEYS"]))
        except ValueError as exc:
            raise CommandError("DOCUMENT_ENCRYPTION_KEYS du fichier actuel n'est pas un JSON valide.") from exc
    if actuel.get("DOCUMENT_ENCRYPTION_KEY"):
        trousseau.setdefault("legacy", actuel["DOCUMENT_ENCRYPTION_KEY"])
    return trousseau


class Command(BaseCommand):
    help = "Écrit .env.prod.nouveau avec des secrets neufs (sans toucher à .env.prod)."

    def add_arguments(self, parser):
        parser.add_argument("--source", default=str(Path(settings.BASE_DIR) / ".env.prod"))
        parser.add_argument("--sortie", default=str(Path(settings.BASE_DIR) / ".env.prod.nouveau"))
        parser.add_argument("--ecraser", action="store_true", help="Remplace un .env.prod.nouveau existant.")

    def handle(self, *args, **options):
        source, sortie = Path(options["source"]), Path(options["sortie"])
        if not source.exists():
            raise CommandError(f"Fichier source introuvable : {source}")
        if sortie.resolve() == source.resolve():
            raise CommandError("La sortie ne peut pas être le fichier source : .env.prod n'est jamais remplacé.")
        if sortie.exists() and not options["ecraser"]:
            raise CommandError(f"{sortie.name} existe déjà (utilisez --ecraser pour le régénérer).")
        actuel = lire_env(source)
        trousseau = ancien_trousseau(actuel)
        if not trousseau:
            raise CommandError("Aucune clé documentaire dans le fichier actuel : impossible de préparer la rotation.")
        nouvelle_id = f"k{timezone.localdate():%Y%m%d}"
        while nouvelle_id in trousseau:
            nouvelle_id += "b"
        trousseau[nouvelle_id] = base64.urlsafe_b64encode(os.urandom(32)).decode()

        lignes = [
            "# ===========================================================================",
            f"# GED — configuration de PRODUCTION préparée le {timezone.localtime():%d/%m/%Y %H:%M}",
            "# Secrets NEUFS. À compléter (« A_REMPLACER »), puis à installer en suivant",
            "# DEPLOIEMENT_SERVEUR.md (section « Renouvellement des secrets »).",
            "# La production refuse de démarrer tant qu'il reste un A_REMPLACER.",
            "# NE JAMAIS partager ce fichier (ni zip, ni e-mail, ni messagerie).",
            "# ===========================================================================",
            "",
            "# --- Domaine et HTTPS (Caddy obtient le certificat automatiquement) --------",
            f"GED_DOMAIN={A_REMPLACER}",
            f"GED_ACME_EMAIL={A_REMPLACER}",
            f"ALLOWED_HOSTS={A_REMPLACER},localhost",
            f"CSRF_TRUSTED_ORIGINS=https://{A_REMPLACER}",
            "CORS_ALLOWED_ORIGINS=",
            "DEBUG=false",
            "SECURE_SSL_REDIRECT=true",
            "GED_ALLOW_INSECURE_HTTP=false",
            "",
            "# --- Secrets régénérés ------------------------------------------------------",
            f"SECRET_KEY={secrets.token_urlsafe(64)}",
            "# Mot de passe NEUF : à appliquer d'abord dans PostgreSQL (ALTER USER, voir guide).",
            f"POSTGRES_PASSWORD={secrets.token_hex(24)}",
            "# Trousseau : la nouvelle clé chiffre les nouveaux dépôts ; l'ancienne (« legacy »)",
            "# reste le temps de lancer `python manage.py rechiffrer_documents`, puis se",
            "# conserve UNIQUEMENT dans le paquet de récupération (anciennes sauvegardes).",
            "DOCUMENT_ENCRYPTION_KEY=",
            f"DOCUMENT_ENCRYPTION_KEYS={json.dumps(trousseau, separators=(',', ':'))}",
            f"DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID={nouvelle_id}",
            "",
            "# --- À régénérer chez les fournisseurs (les anciens ont circulé) ------------",
            "# Gmail : révoquer l'ancien « mot de passe d'application » et en créer un neuf.",
            f"EMAIL_HOST_PASSWORD={A_REMPLACER}",
            "# Google Cloud Console : réinitialiser le secret du client OAuth.",
            f"GOOGLE_OAUTH_CLIENT_SECRET={A_REMPLACER}",
            f"GOOGLE_OAUTH_REDIRECT_URI=https://{A_REMPLACER}/auth/oauth/google/callback",
            "",
            "# --- Premier compte : désactivé (le compte notaire existe déjà) -------------",
            "CREATE_INITIAL_ADMIN=false",
            "",
            "# --- Copie hors site des sauvegardes (stockage S3 compatible) ---------------",
            "BACKUP_CLOUD_ENABLED=true",
            f"BACKUP_CLOUD_BUCKET={A_REMPLACER}",
            "BACKUP_CLOUD_PREFIX=ged",
            f"BACKUP_CLOUD_REGION={A_REMPLACER}",
            f"BACKUP_CLOUD_ENDPOINT={A_REMPLACER}",
            f"BACKUP_CLOUD_ACCESS_KEY={A_REMPLACER}",
            f"BACKUP_CLOUD_SECRET_KEY={A_REMPLACER}",
            "",
            "# --- Réglages repris de l'ancien fichier -----------------------------------",
        ]
        lignes += [f"{cle}={actuel[cle]}" for cle in REPRIS if cle in actuel]
        sortie.write_text("\n".join(lignes) + "\n", encoding="utf-8")
        try:
            os.chmod(sortie, 0o600)
        except OSError:
            pass
        restants = sum(ligne.count(A_REMPLACER) for ligne in lignes)
        self.stdout.write(self.style.SUCCESS(
            f"{sortie.name} écrit : nouveaux SECRET_KEY, mot de passe PostgreSQL et clé documentaire « {nouvelle_id} » "
            f"(ancienne clé conservée pour le rechiffrement). {restants} valeur(s) « {A_REMPLACER} » à compléter. "
            "Aucun secret n'a été affiché."))
