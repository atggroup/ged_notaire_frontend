"""Central place that actually creates notifications.

Tout ce qui doit informer quelqu'un passe par `emit` (ou ses raccourcis
`notify` / `notify_admins`) :

- la notification interne est créée dans la transaction de l'événement ;
- `event_key` rend l'émission idempotente — un travail rejoué ne renvoie pas
  deux fois la même alerte ;
- la doublure e-mail n'est **pas** envoyée ici : elle est mise en file et
  partie par le travail `envoi_emails`, avec relances. Une boîte injoignable
  ne bloque donc ni la requête, ni les rappels suivants, et ne fait plus
  perdre le message.

Règle de contenu des e-mails : ils quittent l'étude. On n'y met jamais un
extrait de document ; les messages rédigés par les automatisations désignent
les dossiers par leur référence, pas par le nom du client.
"""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Notification

# Délais entre deux tentatives d'envoi (minutes) ; au-delà : échec définitif.
EMAIL_BACKOFF_MINUTES = [1, 5, 15, 60, 240]


def _email_voulu(email: bool | None, severity: str) -> bool:
    if not getattr(settings, "NOTIFICATIONS_EMAIL_ENABLED", True):
        return False
    if email is not None:
        return email
    return severity in {Notification.Severity.HIGH, Notification.Severity.CRITICAL}


def emit(recipients, type_: str, title: str, message: str, *, severity: str = Notification.Severity.INFO,
         target_type: str = "", target_id: str = "", email: bool | None = None,
         event_key: str | None = None) -> list[Notification]:
    """Crée une notification par destinataire actif (dédoublonnés).

    `event_key` : si fourni, chaque destinataire ne reçoit l'événement qu'une
    seule fois, quel que soit le nombre d'exécutions."""
    from core.coordination import claim_once

    if recipients is None:
        return []
    if not isinstance(recipients, (list, tuple, set)) and not hasattr(recipients, "__iter__"):
        recipients = [recipients]
    vus, crees = set(), []
    envoyer = _email_voulu(email, severity)
    with transaction.atomic():
        for user in recipients:
            if user is None or user.pk in vus or not getattr(user, "is_active", True):
                continue
            vus.add(user.pk)
            if event_key and not claim_once(f"notif:{event_key}:{user.pk}"):
                continue
            crees.append(Notification.objects.create(
                recipient=user, type=type_, title=title[:255], message=message,
                severity=severity, target_type=target_type, target_id=str(target_id or "")[:80],
                email_status=Notification.EmailStatus.PENDING if envoyer and user.email else Notification.EmailStatus.NONE,
                next_email_at=timezone.now() if envoyer and user.email else None,
            ))
    return crees


def active_admins(exclude=None):
    from accounts.models import User
    admins = User.objects.filter(role=User.Role.ADMIN, is_active=True)
    if exclude is not None:
        admins = admins.exclude(pk=exclude.pk)
    return list(admins)


def notify(user, type_: str, title: str, message: str, **options) -> Notification | None:
    """Create a single real notification for one recipient."""
    if not user:
        return None
    crees = emit([user], type_, title, message, **options)
    return crees[0] if crees else None


def notify_admins(type_: str, title: str, message: str, *, exclude=None, **options) -> list[Notification]:
    """Create a notification for every active notaire · admin account.

    Used whenever something new lands that needs the notaire's validation
    (a document pending review, an access request) so it shows up for them
    even if they never had it triggered themselves.
    """
    return emit(active_admins(exclude), type_, title, message, **options)


def send_pending_emails(limit: int = 50) -> dict:
    """Envoie la file des e-mails dus. Appelé par le travail `envoi_emails`."""
    from django.core.mail import send_mail

    now = timezone.now()
    dues = list(
        Notification.objects.select_related("recipient")
        .filter(email_status=Notification.EmailStatus.PENDING, next_email_at__lte=now)
        .order_by("next_email_at")[:limit]
    )
    envoyes = echecs = 0
    for notif in dues:
        try:
            send_mail(f"GED — {notif.title}", _corps_email(notif), settings.DEFAULT_FROM_EMAIL,
                      [notif.recipient.email], fail_silently=False)
        except Exception as exc:  # noqa: BLE001 — toute panne SMTP est retentée
            notif.email_attempts += 1
            notif.email_error = str(exc)[:500]
            if notif.email_attempts >= len(EMAIL_BACKOFF_MINUTES):
                notif.email_status = Notification.EmailStatus.FAILED
                notif.next_email_at = None
                echecs += 1
            else:
                notif.next_email_at = now + timedelta(minutes=EMAIL_BACKOFF_MINUTES[notif.email_attempts])
            notif.save(update_fields=["email_attempts", "email_error", "email_status", "next_email_at"])
            continue
        notif.email_status = Notification.EmailStatus.SENT
        notif.emailed_at = timezone.now()
        notif.email_attempts += 1
        notif.email_error = ""
        notif.save(update_fields=["email_status", "emailed_at", "email_attempts", "email_error"])
        envoyes += 1
    if echecs:
        # Une panne de messagerie durable doit se voir dans l'application.
        emit(active_admins(), "email_failure", "E-mails non distribués",
             f"{echecs} e-mail(s) de notification n'ont pas pu être envoyés après {len(EMAIL_BACKOFF_MINUTES)} tentatives. "
             "Vérifiez la configuration SMTP (écran Sauvegarde · Supervision).",
             severity=Notification.Severity.HIGH, email=False,
             event_key=f"email-failure:{timezone.localdate().isoformat()}")
    return {"items": envoyes, "message": f"{envoyes} e-mail(s) envoyé(s), {echecs} en échec définitif, "
                                          f"{max(0, len(dues) - envoyes - echecs)} replanifié(s)."}


def _corps_email(notif: Notification) -> str:
    return (
        f"{notif.message}\n\n"
        "Connectez-vous à la GED de l'étude pour consulter le détail.\n"
        "— Message automatique, merci de ne pas y répondre."
    )
