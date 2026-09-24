import os

from .base import *  # noqa

DEBUG = False
# Cache PARTAGÉ par tous les processus (gunicorn, exécutants) : les compteurs
# de limitation de débit y vivent. Le cache mémoire par défaut de Django est
# propre à chaque processus — avec 3 workers, la limite réelle était triplée
# et un compteur n'était jamais vu par les autres processus.
# Table créée par `manage.py createcachetable` (entrypoint Docker).
CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "ged_cache"}}


class ConfigurationProductionRefusee(RuntimeError):
    """La production refuse de démarrer plutôt que de tourner mal configurée."""


# Dérogation EXPLICITE, réservée à un essai local sans certificat
# (http://localhost). Jamais sur un serveur joignable depuis Internet.
ESSAI_LOCAL = env.bool("GED_ALLOW_INSECURE_HTTP", default=False)

if not SECRET_KEY:
    raise ConfigurationProductionRefusee("SECRET_KEY is required in production")
if not ALLOWED_HOSTS:
    raise ConfigurationProductionRefusee("ALLOWED_HOSTS is required in production")
# Le trousseau (DOCUMENT_ENCRYPTION_KEYS) suffit : c'est lui qui permet la
# rotation de clé. L'ancienne variable seule reste acceptée.
if not (DOCUMENT_ENCRYPTION_KEY or DOCUMENT_ENCRYPTION_KEYS):
    raise ConfigurationProductionRefusee("DOCUMENT_ENCRYPTION_KEY ou DOCUMENT_ENCRYPTION_KEYS est requis en production")
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and not (
    EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD
):
    # Les codes MFA et les invitations dépendent de l'email : une config
    # incomplète doit bloquer le démarrage plutôt qu'échouer en silence à
    # la première tentative de connexion d'un utilisateur.
    raise ConfigurationProductionRefusee(
        "EMAIL_HOST, EMAIL_HOST_USER et EMAIL_HOST_PASSWORD sont requis "
        "en production lorsque EMAIL_BACKEND est le backend SMTP"
    )

# --- Aucun champ laissé « A_REMPLACER » (voir preparer_secrets_production) ----
_a_completer = sorted(nom for nom, valeur in os.environ.items() if "A_REMPLACER" in valeur)
if _a_completer:
    raise ConfigurationProductionRefusee(
        "Configuration incomplète — valeurs encore à remplacer : " + ", ".join(_a_completer)
        + " (voir DEPLOIEMENT_SERVEUR.md)."
    )

# --- Antivirus des dépôts ------------------------------------------------------
# Une pièce déposée finit sur les postes de l'étude : sans analyse, un fichier
# piégé transmis par un client y serait redistribué. Renoncer à l'antivirus
# exige une décision explicite (ANTIVIRUS_DESACTIVE=true).
if not ANTIVIRUS_HOST and not env.bool("ANTIVIRUS_DESACTIVE", default=False) and not ESSAI_LOCAL:
    raise ConfigurationProductionRefusee(
        "ANTIVIRUS_HOST est vide : les dépôts ne seraient pas analysés. Utilisez le service « clamav » "
        "du docker-compose (ANTIVIRUS_HOST=clamav) ou, en connaissance de cause, ANTIVIRUS_DESACTIVE=true."
    )

# --- HTTPS obligatoire -------------------------------------------------------
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
if not SECURE_SSL_REDIRECT and not ESSAI_LOCAL:
    raise ConfigurationProductionRefusee(
        "SECURE_SSL_REDIRECT=false : les jetons de session et les actes circuleraient en clair. "
        "Mettez SECURE_SSL_REDIRECT=true (Caddy fournit le HTTPS, voir DEPLOIEMENT_SERVEUR.md). "
        "Pour un essai local uniquement : GED_ALLOW_INSECURE_HTTP=true."
    )
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31_536_000) if SECURE_SSL_REDIRECT else 0
# Ni sous-domaines ni « preload » par défaut : l'inscription sur la liste de
# préchargement des navigateurs est quasi irréversible et engage tout le
# domaine de l'étude. À n'activer qu'en connaissance de cause.
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)
# La sonde de santé de nginx interroge l'API en HTTP interne : elle ne doit
# pas être redirigée (elle échouerait et le conteneur serait déclaré malade).
SECURE_REDIRECT_EXEMPT = [r"^api/health$"]

# --- Pas de joker ni de tunnel temporaire -------------------------------------
# `.trycloudflare.com` ou `*.exemple.com` acceptent N'IMPORTE QUEL sous-domaine,
# y compris ceux qu'un tiers peut créer en quelques secondes (tunnels gratuits) :
# en CSRF, c'est une origine arbitraire déclarée de confiance.
TUNNELS_TEMPORAIRES = ("trycloudflare.com", "ngrok", "loca.lt", "localtunnel")


def _joker(valeur: str) -> bool:
    return valeur.startswith(".") or "*" in valeur


def _temporaire(valeur: str) -> bool:
    return any(t in valeur for t in TUNNELS_TEMPORAIRES)


if not ESSAI_LOCAL:
    problemes = [h for h in ALLOWED_HOSTS if _joker(h) or _temporaire(h) or h == "*"]
    problemes += [o for o in CSRF_TRUSTED_ORIGINS if _joker(o.split("://")[-1]) or _temporaire(o) or not o.startswith("https://")]
    problemes += [o for o in CORS_ALLOWED_ORIGINS if _temporaire(o) or not o.startswith("https://")]
    if problemes:
        raise ConfigurationProductionRefusee(
            "Configuration de production refusée — hôtes ou origines génériques, temporaires ou non HTTPS : "
            + ", ".join(problemes)
            + ". Indiquez le domaine exact de l'étude (ex. ALLOWED_HOSTS=ged.mon-etude.ci, "
              "CSRF_TRUSTED_ORIGINS=https://ged.mon-etude.ci)."
        )
    if _temporaire(GOOGLE_OAUTH_REDIRECT_URI):
        raise ConfigurationProductionRefusee(
            "GOOGLE_OAUTH_REDIRECT_URI pointe vers un tunnel temporaire : utilisez https://<domaine>/auth/oauth/google/callback."
        )
else:
    import warnings
    warnings.warn(
        "GED_ALLOW_INSECURE_HTTP=true : mode ESSAI LOCAL (HTTP, contrôles de domaine désactivés). "
        "Ne jamais déployer une étude notariale avec ce réglage.",
        stacklevel=2,
    )
