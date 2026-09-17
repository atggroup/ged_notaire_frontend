"""Central place that actually creates notifications.

Until now the ``Notification`` model and its read/list endpoints existed
but nothing in the codebase ever called ``Notification.objects.create`` —
so the notification bell and the notifications page were always empty.
Every place in the app that should make someone aware of something calls
one of the two helpers below.
"""
from .models import Notification


def notify(user, type_: str, title: str, message: str) -> Notification | None:
    """Create a single real notification for one recipient."""
    if not user:
        return None
    return Notification.objects.create(recipient=user, type=type_, title=title, message=message)


def notify_admins(type_: str, title: str, message: str, *, exclude=None) -> None:
    """Create a notification for every active notaire · admin account.

    Used whenever something new lands that needs the notaire's validation
    (a document pending review, an access request) so it shows up for them
    even if they never had it triggered themselves.
    """
    from accounts.models import User
    admins = User.objects.filter(role=User.Role.ADMIN, is_active=True)
    if exclude is not None:
        admins = admins.exclude(pk=exclude.pk)
    for admin in admins:
        notify(admin, type_, title, message)
