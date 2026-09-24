from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET


@require_GET
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    # L'état des exécutants est informatif : un worker arrêté ne rend pas
    # l'API indisponible (le statut global reste « ok » pour nginx), mais il
    # doit se voir. Aucun détail interne n'est exposé à un appelant anonyme.
    try:
        from core.models import WorkerHeartbeat
        seuil = timezone.now() - timedelta(seconds=getattr(settings, "WORKER_STALE_SECONDS", 180))
        battements = list(WorkerHeartbeat.objects.values_list("last_seen", flat=True))
        workers = "absent" if not battements else ("ok" if all(b >= seuil for b in battements) else "stale")
    except Exception:  # noqa: BLE001 — table pas encore migrée
        workers = "unknown"
    return JsonResponse({"status": "ok", "database": "ok", "workers": workers})
