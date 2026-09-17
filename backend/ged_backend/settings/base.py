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
    "accounts", "dossiers", "documents", "permissions_app", "audit", "notifications", "settings_app",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware", "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "ged_backend.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.request", "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "ged_backend.wsgi.application"
ASGI_APPLICATION = "ged_backend.asgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
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
    "DEFAULT_THROTTLE_CLASSES": ("accounts.throttles.AuthRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {"auth": "20/minute"},
}
SIMPLE_JWT = {"ACCESS_TOKEN_LIFETIME": timedelta(hours=8), "AUTH_HEADER_TYPES": ("Bearer",)}
SPECTACULAR_SETTINGS = {"TITLE": "GED Cabinet Notarial API", "VERSION": "1.0.0", "SERVE_INCLUDE_SCHEMA": False}

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
    },
}
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
