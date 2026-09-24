"""Registre et exécution des travaux automatiques de la GED.

Chaque travail est une fonction décorée par `@job(...)` dans le `tasks.py`
de son application. L'exécution (`run_job`) garantit, pour tous :

- un seul exécutant à la fois (verrou à bail, sûr avec plusieurs conteneurs) ;
- une trace `JobRun` (statut, durée, volume, erreur) visible à l'écran ;
- une entrée au journal d'audit dès qu'il a agi ou échoué ;
- en cas d'échec, une notification aux notaires — jamais de panne muette ;
- des relances planifiées, dont l'état vit en base : un redémarrage du
  conteneur ne fait ni perdre ni rejouer une échéance.
"""
import logging
import os
import socket
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Callable

from django.utils import timezone
from django.utils.module_loading import autodiscover_modules

from .coordination import acquire_lock, release_lock
from .models import JobRun

logger = logging.getLogger("ged.jobs")

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


@dataclass
class JobSpec:
    name: str
    func: Callable
    label: str
    group: str = "defaut"
    every: int | None = None            # secondes
    daily_at: str | None = None          # "HH:MM", heure locale de l'étude
    weekday: int | None = None           # 0 = lundi ; avec daily_at → hebdomadaire
    retries: int = 0                     # relances après un échec
    retry_delay: int = 300               # secondes entre deux tentatives
    lock_ttl: int = 900                  # durée maximale d'une exécution
    alert_on_failure: bool = True
    extra: dict = field(default_factory=dict)

    def schedule_label(self) -> str:
        if self.every:
            minutes = self.every // 60
            if minutes >= 60 and minutes % 60 == 0:
                return f"toutes les {minutes // 60} h"
            return f"toutes les {minutes} min" if minutes else f"toutes les {self.every} s"
        if self.weekday is not None:
            return f"chaque {JOURS[self.weekday]} à {self.daily_at}"
        return f"chaque jour à {self.daily_at}"


REGISTRY: dict[str, JobSpec] = {}
_discovered = False


def job(name: str, label: str, **options):
    """Enregistre une fonction `f() -> dict | int | None` comme travail.

    Le résultat peut être un entier (volume traité) ou un dict
    `{"items": n, "message": "..."}`."""
    def decorator(func):
        REGISTRY[name] = JobSpec(name=name, func=func, label=label, **options)
        return func
    return decorator


def registry() -> dict[str, JobSpec]:
    global _discovered
    if not _discovered:
        autodiscover_modules("tasks")
        _discovered = True
    return REGISTRY


def _host() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"[:120]


# --------------------------------------------------------------------------
# Échéancier
# --------------------------------------------------------------------------

def _slot_start(spec: JobSpec, now: datetime) -> datetime:
    """Début du créneau courant d'un travail à heure fixe (heure locale)."""
    local = timezone.localtime(now)
    hh, mm = (int(x) for x in spec.daily_at.split(":"))
    slot = timezone.make_aware(datetime.combine(local.date(), time(hh, mm)), timezone.get_current_timezone())
    if spec.weekday is not None:
        slot -= timedelta(days=(local.weekday() - spec.weekday) % 7)
        if slot > now:
            slot -= timedelta(days=7)
    elif slot > now:
        slot -= timedelta(days=1)
    return slot


def is_due(spec: JobSpec, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    runs = JobRun.objects.filter(name=spec.name).exclude(status=JobRun.Status.REQUESTED)
    if spec.every:
        last = runs.order_by("-started_at").first()
        if last is None:
            return True
        if last.status == JobRun.Status.RUNNING:
            return False
        reference = last.finished_at or last.started_at
        if last.status == JobRun.Status.FAILURE and spec.retries:
            return now >= reference + timedelta(seconds=min(spec.retry_delay, spec.every))
        return now >= last.started_at + timedelta(seconds=spec.every)
    slot = _slot_start(spec, now)
    since = list(runs.filter(started_at__gte=slot).order_by("started_at"))
    if any(r.status in {JobRun.Status.SUCCESS, JobRun.Status.RUNNING} for r in since):
        return False
    failures = [r for r in since if r.status == JobRun.Status.FAILURE]
    if not failures:
        return True
    if len(failures) > spec.retries:
        return False  # tentatives épuisées : l'alerte est déjà partie
    return now >= (failures[-1].finished_at or failures[-1].started_at) + timedelta(seconds=spec.retry_delay)


def next_run_estimate(spec: JobSpec, now: datetime | None = None) -> datetime | None:
    now = now or timezone.now()
    if spec.every:
        last = JobRun.objects.filter(name=spec.name, started_at__isnull=False).order_by("-started_at").first()
        return (last.started_at + timedelta(seconds=spec.every)) if last else now
    slot = _slot_start(spec, now)
    return slot + timedelta(days=7 if spec.weekday is not None else 1) if not is_due(spec, now) else now


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------

def run_job(name: str, *, trigger: str = JobRun.Trigger.SCHEDULED, user=None, run: JobRun | None = None) -> JobRun | None:
    """Exécute un travail. Renvoie None si un autre exécutant le tient déjà."""
    spec = registry()[name]
    token = acquire_lock(f"job:{name}", spec.lock_ttl)
    if token is None:
        if run is not None:
            # Demande manuelle arrivée pendant une exécution : elle est
            # satisfaite par celle-ci, on la clôt sans relancer.
            run.status, run.finished_at = JobRun.Status.SUCCESS, timezone.now()
            run.message = "Déjà en cours d'exécution : la demande est couverte par l'exécution en cours."
            run.save(update_fields=["status", "finished_at", "message"])
        return None
    previous_failures = _consecutive_failures(name)
    if run is None:
        run = JobRun.objects.create(name=name, trigger=trigger, triggered_by=user, status=JobRun.Status.RUNNING,
                                    started_at=timezone.now(), host=_host(), attempt=previous_failures + 1)
    else:
        run.status, run.started_at, run.host, run.attempt = JobRun.Status.RUNNING, timezone.now(), _host(), previous_failures + 1
        run.save(update_fields=["status", "started_at", "host", "attempt"])
    try:
        result = spec.func()
        items, message = _normalise(result)
        run.status, run.items, run.message = JobRun.Status.SUCCESS, items, message
    except Exception as exc:  # noqa: BLE001 — l'échec est capturé, tracé et signalé
        logger.exception("Travail %s en échec", name)
        run.status = JobRun.Status.FAILURE
        run.error = "".join(traceback.format_exception_only(type(exc), exc)).strip()[:4000]
        run.message = f"Échec : {str(exc)[:500]}"
    finally:
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "items", "message", "error", "finished_at"])
        release_lock(f"job:{name}", token)
    _trace(spec, run, user)
    return run


def _consecutive_failures(name: str) -> int:
    count = 0
    for status in JobRun.objects.filter(name=name).exclude(status__in=[JobRun.Status.REQUESTED, JobRun.Status.RUNNING]).order_by("-started_at").values_list("status", flat=True)[:20]:
        if status != JobRun.Status.FAILURE:
            break
        count += 1
    return count


def _normalise(result) -> tuple[int, str]:
    if isinstance(result, dict):
        return int(result.get("items", 0) or 0), str(result.get("message", ""))[:2000]
    if isinstance(result, int):
        return result, f"{result} élément(s) traité(s)."
    return 0, ""


def _trace(spec: JobSpec, run: JobRun, user) -> None:
    from audit.services import log_system_event
    from notifications.models import Notification
    from notifications.services import active_admins, emit

    failed = run.status == JobRun.Status.FAILURE
    # Un passage à vide toutes les minutes n'a rien à faire au journal
    # probatoire ; ce qui a agi, échoué ou été demandé par un humain, si.
    if failed or run.items or run.trigger != JobRun.Trigger.SCHEDULED:
        log_system_event(f"job_{spec.name}", "job", str(run.pk), result="failure" if failed else "success",
                         metadata={"travail": spec.name, "elements": run.items, "tentative": run.attempt,
                                   "declencheur": run.trigger, "message": run.message[:300]}, user=user)
    if not failed or not spec.alert_on_failure:
        return
    final = spec.every is not None or run.attempt > spec.retries
    suite = "Plus de nouvelle tentative automatique : intervention requise." if final else \
        f"Nouvelle tentative automatique dans {max(1, spec.retry_delay // 60)} min."
    emit(active_admins(), "job_failure", f"Échec : {spec.label}",
         f"Le travail automatique « {spec.label} » a échoué (tentative {run.attempt}). {suite} Détail : {run.message[:300]}",
         severity=Notification.Severity.CRITICAL if final else Notification.Severity.HIGH,
         target_type="job", target_id=spec.name,
         # Une alerte par travail et par jour suffit, même s'il échoue chaque minute.
         event_key=f"job-failure:{spec.name}:{timezone.localdate().isoformat()}:{'final' if final else 'retry'}")
