from .base import *  # noqa

DEBUG = False
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is required in production")
if not ALLOWED_HOSTS:
    raise RuntimeError("ALLOWED_HOSTS is required in production")
if not DOCUMENT_ENCRYPTION_KEY:
    raise RuntimeError("DOCUMENT_ENCRYPTION_KEY is required in production")
if EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and not (
    EMAIL_HOST and EMAIL_HOST_USER and EMAIL_HOST_PASSWORD
):
    # Les codes MFA et les invitations dépendent de l'email : une config
    # incomplète doit bloquer le démarrage plutôt qu'échouer en silence à
    # la première tentative de connexion d'un utilisateur.
    raise RuntimeError(
        "EMAIL_HOST, EMAIL_HOST_USER et EMAIL_HOST_PASSWORD sont requis "
        "en production lorsque EMAIL_BACKEND est le backend SMTP"
    )
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = env.bool("SECURE_SSL_REDIRECT", default=True)
CSRF_COOKIE_SECURE = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 31_536_000 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_SSL_REDIRECT
SECURE_HSTS_PRELOAD = SECURE_SSL_REDIRECT
if not SECURE_SSL_REDIRECT:
    import warnings
    warnings.warn(
        "SECURE_SSL_REDIRECT=false : à n'utiliser que pour un test local "
        "sans certificat TLS (ex. docker compose sur http://localhost). "
        "Ne jamais déployer une étude notariale sur un vrai domaine avec "
        "ce réglage à false.",
        stacklevel=2,
    )
