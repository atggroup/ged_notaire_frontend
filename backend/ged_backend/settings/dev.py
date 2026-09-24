from .base import *  # noqa
import secrets

# Development is explicit; production settings never enable DEBUG by default.
DEBUG = env.bool("DEBUG", default=False)
# A local run remains usable before a .env file exists. Production always requires a persistent secret.
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
SERVE_FRONTEND = env.bool("SERVE_FRONTEND", default=True)

# En développement, admin et documentation de l'API suivent DEBUG.
DJANGO_ADMIN_ENABLED = env.bool("DJANGO_ADMIN_ENABLED", default=DEBUG)
API_DOCS_ENABLED = env.bool("API_DOCS_ENABLED", default=DEBUG)
