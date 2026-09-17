from .base import *  # noqa
import secrets

# Development is explicit; production settings never enable DEBUG by default.
DEBUG = env.bool("DEBUG", default=False)
# A local run remains usable before a .env file exists. Production always requires a persistent secret.
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)
SERVE_FRONTEND = env.bool("SERVE_FRONTEND", default=True)
