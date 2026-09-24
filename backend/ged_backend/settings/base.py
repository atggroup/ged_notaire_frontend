"""Shared, secure Django settings for the GED API."""
from datetime import timedelta
from pathlib import Path
import environ

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "corsheaders", "rest_framework", "drf_spectacular",
    "accounts", "dossiers", "documents", "permissions_app", "audit", "notifications", "settings_app", "core",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "ged_backend.securite.SecurityHeadersMiddleware",
]
ROOT_URLCONF = "ged_backend.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "ged_backend.wsgi.application"
ASGI_APPLICATION = "ged_backend.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
AUTH_USER_MODEL = "accounts.User"
# Ces validateurs ne servent que s'ils sont appelés : `PasswordSerializer`
# (accounts/serializers.py) les invoque explicitement sur l'activation de
# compte et sur la réinitialisation — les deux seuls chemins par lesquels un
# mot de passe entre dans l'application.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
]
PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher", "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Abidjan"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("accounts.authentication.SessionVersionJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # Routes publiques : 20/min par adresse et par route (anti-force-brute).
    # Utilisateur connecté : budget PAR COMPTE, indépendant de l'adresse —
    # tous les postes d'une étude partagent souvent la même IP (NAT).
    "DEFAULT_THROTTLE_CLASSES": ("accounts.throttles.EtudeRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {"auth": env("THROTTLE_AUTH_RATE", default="20/minute"),
                               "user": env("THROTTLE_USER_RATE", default="600/minute")},
    # Filet supplémentaire pour les limiteurs de DRF que nous ne surchargeons
    # pas : `AuthRateThrottle` détermine l'adresse par `ged_backend.reseau`,
    # qui ignore purement et simplement X-Forwarded-For.
    "NUM_PROXIES": env.int("NUM_PROXIES", default=1),
}

# En-tête, posé par NOTRE reverse proxy, qui porte l'adresse réelle du client.
# nginx l'écrit avec `proxy_set_header X-Real-IP $remote_addr` : contrairement
# à X-Forwarded-For, `proxy_set_header` REMPLACE ce que le navigateur a pu
# envoyer, la valeur n'est donc pas falsifiable.
# Mettre la chaîne vide si l'application est exposée SANS reverse proxy : on se
# rabat alors sur REMOTE_ADDR. Ne jamais y mettre HTTP_X_FORWARDED_FOR.
TRUSTED_CLIENT_IP_HEADER = env("TRUSTED_CLIENT_IP_HEADER", default="HTTP_X_REAL_IP")
SIMPLE_JWT = {"ACCESS_TOKEN_LIFETIME": timedelta(hours=8), "AUTH_HEADER_TYPES": ("Bearer",)}
SPECTACULAR_SETTINGS = {"TITLE": "GED Cabinet Notarial API", "VERSION": "1.0.0", "SERVE_INCLUDE_SCHEMA": False,
                        # Même activé, le schéma n'est servi qu'à un utilisateur connecté.
                        "SERVE_PERMISSIONS": ["rest_framework.permissions.IsAuthenticated"]}
# Admin Django et documentation de l'API : désactivés sauf demande explicite
# (en développement, ils suivent DEBUG). Voir ged_backend/urls.py.
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=False)
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=False)

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@ged.local")
# SMTP réel : n'a d'effet que si EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend.
# EMAIL_USE_TLS (port 587, STARTTLS) et EMAIL_USE_SSL (port 465, SSL implicite) sont
# mutuellement exclusifs — n'en activer qu'un seul selon ce que demande le fournisseur.
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DOCUMENT_ENCRYPTION_KEY = env("DOCUMENT_ENCRYPTION_KEY", default="")
# Key rotation (trousseau) : DOCUMENT_ENCRYPTION_KEYS est un objet JSON
# {key_id: clé_base64} qui peut contenir plusieurs générations de clés en
# même temps ; DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID choisit laquelle chiffre
# les nouveaux documents. Les anciens documents restent lisibles avec leur
# propre clé tant qu'elle reste présente dans le trousseau — ne jamais
# supprimer une clé encore référencée par un document. Voir
# CONFORMITE_RGPD_ARTCI.md pour la politique de rotation recommandée.
DOCUMENT_ENCRYPTION_KEYS = env("DOCUMENT_ENCRYPTION_KEYS", default="")
DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID = env("DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID", default="")
DOCUMENT_MAX_UPLOAD_BYTES = env.int("DOCUMENT_MAX_UPLOAD_BYTES", default=25 * 1024 * 1024)
OCR_DPI = env.int("OCR_DPI", default=300)
OCR_LANG = env("OCR_LANG", default="fra+eng")
OCR_MAX_PAGES = env.int("OCR_MAX_PAGES", default=50)
BACKUP_CLOUD_ENABLED = env.bool("BACKUP_CLOUD_ENABLED", default=False)
BACKUP_LOCAL_ROOT = env("BACKUP_LOCAL_ROOT", default=str(BASE_DIR / "backup_store"))
BACKUP_CLOUD_BUCKET = env("BACKUP_CLOUD_BUCKET", default="")
BACKUP_CLOUD_PREFIX = env("BACKUP_CLOUD_PREFIX", default="ged")
BACKUP_CLOUD_REGION = env("BACKUP_CLOUD_REGION", default="us-east-1")
BACKUP_CLOUD_ENDPOINT = env("BACKUP_CLOUD_ENDPOINT", default="")
BACKUP_CLOUD_ACCESS_KEY = env("BACKUP_CLOUD_ACCESS_KEY", default="")
BACKUP_CLOUD_SECRET_KEY = env("BACKUP_CLOUD_SECRET_KEY", default="")
SERVE_FRONTEND = env.bool("SERVE_FRONTEND", default=False)
# Antivirus des dépôts (ClamAV / clamd). Vide = pas d'analyse (développement).
# En production, le service `clamav` du docker-compose est exigé.
ANTIVIRUS_HOST = env("ANTIVIRUS_HOST", default="")
ANTIVIRUS_PORT = env.int("ANTIVIRUS_PORT", default=3310)
ANTIVIRUS_TIMEOUT = env.int("ANTIVIRUS_TIMEOUT", default=60)
# Moteur injoignable : refuser le dépôt (true) plutôt que l'accepter non analysé.
ANTIVIRUS_REQUIRED = env.bool("ANTIVIRUS_REQUIRED", default=True)

# --- Automatisations (app `core`, commande `run_worker`) -------------------
# Doublure e-mail des notifications (rappels, alertes). Les e-mails partent
# d'une file avec relances ; désactiver si aucun SMTP n'est configuré.
NOTIFICATIONS_EMAIL_ENABLED = env.bool("NOTIFICATIONS_EMAIL_ENABLED", default=True)
NOTIFICATION_RETENTION_DAYS = env.int("NOTIFICATION_RETENTION_DAYS", default=180)
JOBRUN_RETENTION_DAYS = env.int("JOBRUN_RETENTION_DAYS", default=90)
# Surveillance EXTERNE (ex. Healthchecks.io) : adresse appelée régulièrement
# par l'exécutant ; son silence ou « /fail » déclenche l'alerte chez le service.
MONITORING_PING_URL = env("MONITORING_PING_URL", default="")
MONITORING_PING_INTERVAL = env.int("MONITORING_PING_INTERVAL", default=300)
# Un exécutant sans signe de vie depuis ce délai est signalé comme arrêté.
WORKER_STALE_SECONDS = env.int("WORKER_STALE_SECONDS", default=180)
# Échéances des tâches : paliers de rappel (jours avant) et escalade (jours après).
TASK_REMINDER_DAYS = [int(x) for x in env.list("TASK_REMINDER_DAYS", default=["7", "3", "1"])]
TASK_ESCALATION_DAYS = env.int("TASK_ESCALATION_DAYS", default=2)
# OCR : volume par passage, tentatives, délai avant de considérer un
# traitement comme bloqué.
OCR_BATCH = env.int("OCR_BATCH", default=5)
OCR_MAX_ATTEMPTS = env.int("OCR_MAX_ATTEMPTS", default=3)
OCR_STUCK_MINUTES = env.int("OCR_STUCK_MINUTES", default=30)
# Checklists : délai (jours) avant relance d'une pièce requise non reçue,
# préavis avant expiration d'une pièce datée, retour d'un original sorti.
CHECKLIST_REMINDER_DAYS = env.int("CHECKLIST_REMINDER_DAYS", default=7)
DOCUMENT_EXPIRY_NOTICE_DAYS = env.int("DOCUMENT_EXPIRY_NOTICE_DAYS", default=30)
PHYSICAL_RETURN_DAYS = env.int("PHYSICAL_RETURN_DAYS", default=14)
# Sauvegardes : fréquence, âge maximal toléré avant alerte, rétention
# (toutes celles des dernières 24 h, puis une par jour / semaine / mois).
BACKUP_INTERVAL_SECONDS = env.int("BACKUP_INTERVAL_SECONDS", default=21600)
BACKUP_MAX_AGE_HOURS = env.int("BACKUP_MAX_AGE_HOURS", default=0)  # 0 = 2 × l'intervalle
BACKUP_RETENTION_DAILY = env.int("BACKUP_RETENTION_DAILY", default=7)
BACKUP_RETENTION_WEEKLY = env.int("BACKUP_RETENTION_WEEKLY", default=4)
BACKUP_RETENTION_MONTHLY = env.int("BACKUP_RETENTION_MONTHLY", default=12)
RESTORE_DRILL_WEEKDAY = env.int("RESTORE_DRILL_WEEKDAY", default=6)  # dimanche
# Surveillance de sécurité : seuils sur fenêtre glissante de 10 minutes.
SECURITY_MASS_VIEW_THRESHOLD = env.int("SECURITY_MASS_VIEW_THRESHOLD", default=40)
SECURITY_MASS_TRASH_THRESHOLD = env.int("SECURITY_MASS_TRASH_THRESHOLD", default=5)
SECURITY_FAILED_LOGIN_THRESHOLD = env.int("SECURITY_FAILED_LOGIN_THRESHOLD", default=5)
SECURITY_OFFICE_HOURS = env("SECURITY_OFFICE_HOURS", default="07:00-20:00")

# Google Sign-In (accounts app). GOOGLE_OAUTH_REDIRECT_URI must match, byte
# for byte, an "Authorized redirect URI" configured on the OAuth client in
# Google Cloud Console — including the http(s) scheme and the absence/
# presence of a trailing slash. It is deliberately *not* under /api/ so it
# can stay stable even if the API prefix ever changes.
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", default="")
GOOGLE_OAUTH_REDIRECT_URI = env("GOOGLE_OAUTH_REDIRECT_URI", default="http://localhost:8000/auth/oauth/google/callback")
# Where a browser lands after the Google round-trip; the page reads its own
# query-string ticket and finishes the login/registration in JS.
FRONTEND_OAUTH_LANDING_PATH = env("FRONTEND_OAUTH_LANDING_PATH", default="/login.html")

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Sans ceci, Django envoie les erreurs 500 vers une notification e-mail aux
# ADMINS (non configurés ici) et rien n'apparaît dans les logs du conteneur
# (`docker compose logs web`), rendant tout incident invisible en production.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "ged.jobs": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
