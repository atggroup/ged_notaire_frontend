"""Development-only static front-end serving without exposing backend sources."""
import mimetypes
from pathlib import Path
from django.conf import settings
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.utils._os import safe_join

FRONTEND_ROOT = settings.BASE_DIR.parent
ALLOWED_SUFFIXES = {".html", ".css", ".js", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".mp4"}
BLOCKED_PARTS = {"backend", ".git", ".env", ".venv", "media"}


def serve_frontend(request, requested_path: str = ""):
    """Serve only whitelisted front-end assets; API and private files stay excluded."""
    if not getattr(settings, "SERVE_FRONTEND", False):
        raise Http404
    if not requested_path:
        return HttpResponseRedirect("/login.html")
    path = Path(requested_path)
    if any(part in BLOCKED_PARTS or part.startswith(".") for part in path.parts) or path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise Http404
    try:
        full_path = Path(safe_join(str(FRONTEND_ROOT), requested_path))
    except Exception as exc:
        raise Http404 from exc
    if not full_path.is_file():
        raise Http404
    content_type, _ = mimetypes.guess_type(full_path.name)
    return FileResponse(full_path.open("rb"), content_type=content_type or "application/octet-stream")
