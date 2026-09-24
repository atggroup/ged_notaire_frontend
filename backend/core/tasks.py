"""Nettoyage technique quotidien.

Liste BLANCHE : seules les données techniques éphémères sont concernées.
Aucun document, aucune entrée du journal d'audit, aucune sauvegarde n'est
touché ici — la conservation des pièces relève exclusivement du circuit
corbeille → demande → autorisation, piloté par le notaire.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import ProtectedError
from django.utils import timezone

from .jobs import job
from .models import JobRun


@job("nettoyage", "Nettoyage technique (codes, invitations, historiques)", daily_at="03:00", retries=1, retry_delay=1800)
def nettoyage():
    from accounts.models import InviteCode, OTPCode, PendingRegistration
    from notifications.models import Notification

    now = timezone.now()
    compte = {}
    compte["codes_otp"] = OTPCode.objects.filter(expires_at__lt=now - timedelta(days=1)).delete()[0]
    compte["inscriptions"] = PendingRegistration.objects.filter(created_at__lt=now - timedelta(days=30)).delete()[0]
    invitations = 0
    for invite in InviteCode.objects.filter(used_at__isnull=True, expires_at__lt=now - timedelta(days=90)):
        try:
            invite.delete()
            invitations += 1
        except ProtectedError:
            continue
    compte["invitations"] = invitations
    jours = getattr(settings, "NOTIFICATION_RETENTION_DAYS", 180)
    compte["notifications_lues"] = Notification.objects.filter(
        read=True, created_at__lt=now - timedelta(days=jours)).exclude(email_status=Notification.EmailStatus.PENDING).delete()[0]
    compte["historique_travaux"] = JobRun.objects.filter(
        requested_at__lt=now - timedelta(days=getattr(settings, "JOBRUN_RETENTION_DAYS", 90))).exclude(
        status__in=[JobRun.Status.RUNNING, JobRun.Status.REQUESTED]).delete()[0]
    try:
        from django.contrib.sessions.models import Session
        compte["sessions"] = Session.objects.filter(expire_date__lt=now).delete()[0]
    except Exception:  # noqa: BLE001 — table des sessions absente : rien à nettoyer
        compte["sessions"] = 0
    total = sum(compte.values())
    return {"items": total, "message": ", ".join(f"{k} : {v}" for k, v in compte.items())}
