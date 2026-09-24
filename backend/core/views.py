"""Supervision des automatisations (réservée au notaire).

GET  /api/automation/jobs                 état de chaque travail + exécutants
GET  /api/automation/jobs/<name>/runs     historique d'un travail
POST /api/automation/jobs/<name>/runs     demande une exécution (202) : elle
                                          est prise par l'exécutant, jamais
                                          lancée dans la requête HTTP.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from audit.services import log_event
from ged_backend.api import ContractSerializer

from .jobs import next_run_estimate, registry
from .models import JobRun, WorkerHeartbeat


class APIView(GenericAPIView):
    serializer_class = ContractSerializer


def _reserve_au_notaire(request):
    if request.user.role != "admin":
        return Response({"detail": "Supervision réservée au notaire."}, status=403)
    return None


def run_payload(run: JobRun) -> dict:
    return {
        "id": run.pk, "name": run.name, "status": run.status, "statusLabel": run.get_status_display(),
        "trigger": run.trigger, "triggeredBy": run.triggered_by.display_name if run.triggered_by_id else None,
        "requestedAt": run.requested_at.isoformat(),
        "startedAt": run.started_at.isoformat() if run.started_at else None,
        "finishedAt": run.finished_at.isoformat() if run.finished_at else None,
        "durationSeconds": run.duration_seconds, "attempt": run.attempt, "items": run.items,
        "message": run.message, "error": run.error[:1000],
    }


def worker_status() -> list[dict]:
    seuil = timezone.now() - timedelta(seconds=getattr(settings, "WORKER_STALE_SECONDS", 180))
    return [{"name": w.name, "host": w.host, "groups": w.groups, "lastSeen": w.last_seen.isoformat(),
             "alive": w.last_seen >= seuil} for w in WorkerHeartbeat.objects.order_by("name")]


class JobListView(APIView):
    def get(self, request):
        refus = _reserve_au_notaire(request)
        if refus:
            return refus
        jobs = []
        for spec in sorted(registry().values(), key=lambda s: (s.group, s.label)):
            last = JobRun.objects.filter(name=spec.name).exclude(status=JobRun.Status.REQUESTED).select_related("triggered_by").first()
            last_success = JobRun.objects.filter(name=spec.name, status=JobRun.Status.SUCCESS).first()
            pending = JobRun.objects.filter(name=spec.name, status=JobRun.Status.REQUESTED).exists()
            nxt = next_run_estimate(spec)
            jobs.append({
                "name": spec.name, "label": spec.label, "group": spec.group, "schedule": spec.schedule_label(),
                "retries": spec.retries, "lastRun": run_payload(last) if last else None,
                "lastSuccessAt": last_success.finished_at.isoformat() if last_success and last_success.finished_at else None,
                "nextRunAt": nxt.isoformat() if nxt else None, "requestPending": pending,
                "healthy": last is None or last.status != JobRun.Status.FAILURE,
            })
        return Response({"workers": worker_status(), "jobs": jobs})


class JobRunsView(APIView):
    def get(self, request, name):
        refus = _reserve_au_notaire(request)
        if refus:
            return refus
        if name not in registry():
            return Response({"detail": "Travail inconnu."}, status=404)
        runs = JobRun.objects.filter(name=name).select_related("triggered_by")[:30]
        return Response([run_payload(r) for r in runs])

    def post(self, request, name):
        refus = _reserve_au_notaire(request)
        if refus:
            return refus
        if name not in registry():
            return Response({"detail": "Travail inconnu."}, status=404)
        run = JobRun.objects.filter(name=name, status=JobRun.Status.REQUESTED).first()
        created = run is None
        if created:
            run = JobRun.objects.create(name=name, status=JobRun.Status.REQUESTED, trigger=JobRun.Trigger.MANUAL,
                                        triggered_by=request.user)
            log_event(request, "job_requested", "job", name, metadata={"run": run.pk})
        return Response(run_payload(run), status=202 if created else 200)
