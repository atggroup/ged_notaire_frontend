"""Signal de vie vers une surveillance EXTERNE (ex. Healthchecks.io).

Les alertes internes supposent que le serveur fonctionne : si la machine, le
réseau ou Docker tombent, personne n'est prévenu. L'exécutant appelle donc
régulièrement une adresse fournie par un service externe ; si les appels
cessent (serveur arrêté) ou signalent un échec (travail en échec, exécutant
d'un groupe muet), c'est ce service qui prévient le notaire (e-mail, SMS).

Réglages : MONITORING_PING_URL (vide = désactivé), MONITORING_PING_INTERVAL.
Convention Healthchecks.io : `<url>` = tout va bien, `<url>/fail` = problème.
"""
import logging
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger("ged.jobs")
_dernier_signal = {"instant": None}


def etat_global() -> tuple[bool, str]:
    """(sain, résumé) : travaux dont la dernière exécution a échoué, exécutants muets."""
    from .models import JobRun, WorkerHeartbeat
    dernieres = {}
    for nom, statut in JobRun.objects.exclude(status__in=[JobRun.Status.REQUESTED, JobRun.Status.RUNNING]).order_by("-started_at").values_list("name", "status")[:500]:
        dernieres.setdefault(nom, statut)
    en_echec = sorted(n for n, s in dernieres.items() if s == JobRun.Status.FAILURE)
    seuil = timezone.now() - timedelta(seconds=getattr(settings, "WORKER_STALE_SECONDS", 180))
    muets = sorted(WorkerHeartbeat.objects.filter(last_seen__lt=seuil).values_list("name", flat=True))
    problemes = ([f"en échec : {', '.join(en_echec)}"] if en_echec else []) + ([f"exécutants muets : {', '.join(muets)}"] if muets else [])
    return not problemes, " ; ".join(problemes) or "tous les travaux sont sains"


def signaler(force: bool = False) -> bool | None:
    """Envoie le signal de vie si l'intervalle est écoulé. None si désactivé."""
    url = (getattr(settings, "MONITORING_PING_URL", "") or "").strip().rstrip("/")
    if not url:
        return None
    if not url.startswith(("https://", "http://")):
        logger.error("MONITORING_PING_URL ignorée : schéma non HTTP(S).")
        return None
    intervalle = timedelta(seconds=getattr(settings, "MONITORING_PING_INTERVAL", 300))
    maintenant = timezone.now()
    if not force and _dernier_signal["instant"] and maintenant - _dernier_signal["instant"] < intervalle:
        return None
    sain, resume = etat_global()
    cible = url if sain else f"{url}/fail"
    try:
        requete = urllib.request.Request(cible, data=resume.encode("utf-8")[:10000], method="POST",
                                         headers={"User-Agent": "GED-notariale/run_worker"})
        with urllib.request.urlopen(requete, timeout=10):  # noqa: S310 — URL fixée par la configuration
            pass
    except Exception as exc:  # noqa: BLE001 — la surveillance externe ne doit jamais arrêter l'exécutant
        logger.warning("Signal de vie non transmis : %s", exc)
        return False
    _dernier_signal["instant"] = maintenant
    return sain
