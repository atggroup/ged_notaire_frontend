"""Échéances et rappels.

Pour chaque tâche ouverte ayant une échéance :

    J-7 → J-3 → J-1 → jour J → retard → escalade (J+2) au notaire superviseur

- un palier n'est émis que s'il avait un sens à la création de la tâche
  (une tâche créée à 2 jours de l'échéance ne reçoit pas de « J-7 ») ;
- si l'exécutant a été arrêté et que plusieurs paliers sont passés, seul le
  plus récent part — personne ne reçoit trois rappels d'un coup ;
- chaque palier est revendiqué une seule fois (`claim_once`) dans la même
  transaction que la notification : rejouer le travail ne double rien ;
- chaque envoi est journalisé (acteur « Système »).

Le rappel manuel (`reminder_at`) choisi dans le formulaire reste honoré.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from audit.services import log_system_event
from core.coordination import claim_once
from core.jobs import job

from .models import Notification, Task
from .services import active_admins, emit, send_pending_emails

OUVERTES = [Task.Status.OPEN, Task.Status.IN_PROGRESS]


def _date(valeur) -> str:
    return timezone.localtime(valeur).strftime("%d/%m/%Y à %Hh%M")


def _debut_du_jour(valeur):
    local = timezone.localtime(valeur)
    return timezone.make_aware(datetime.combine(local.date(), time.min), timezone.get_current_timezone())


def _suffixe_dossier(tache) -> str:
    return f" — dossier {tache.dossier.reference}" if tache.dossier_id else ""


def paliers_de(tache, now):
    """Liste ordonnée des paliers atteints et applicables : [(clé, libellé)]."""
    if not tache.due_at or now >= tache.due_at:
        return []
    atteints = []
    for jours in sorted(set(getattr(settings, "TASK_REMINDER_DAYS", [7, 3, 1])), reverse=True):
        instant = tache.due_at - timedelta(days=jours)
        if tache.created_at <= instant <= now:
            atteints.append((f"J-{jours}", f"échéance dans {jours} jour{'s' if jours > 1 else ''}"))
    debut = _debut_du_jour(tache.due_at)
    if tache.created_at <= debut <= now:
        atteints.append(("J0", "échéance aujourd'hui"))
    return atteints


def envoyer_rappels_taches(now=None) -> dict:
    now = now or timezone.now()
    rappels = paliers = retards = escalades = 0

    # 1. Rappel manuel choisi par l'utilisateur (comportement historique).
    for tache in Task.objects.filter(status__in=OUVERTES, reminder_at__isnull=False, reminder_at__lte=now,
                                     reminder_sent_at__isnull=True).select_related("assigned_to", "dossier"):
        with transaction.atomic():
            if not claim_once(f"task:{tache.pk}:rappel-manuel"):
                continue
            corps = f"« {tache.title} »{_suffixe_dossier(tache)}" + (
                f" — échéance le {_date(tache.due_at)}." if tache.due_at else " — sans échéance.")
            emit([tache.assigned_to], "task_reminder", "Rappel de tâche", corps, email=True,
                 target_type="task", target_id=tache.pk)
            tache.reminder_sent_at = now
            tache.save(update_fields=["reminder_sent_at"])
            log_system_event("task_reminder_sent", "task", str(tache.pk), metadata={"palier": "manuel"})
        rappels += 1

    # 2. Paliers avant échéance.
    for tache in Task.objects.filter(status__in=OUVERTES, due_at__gt=now, due_at__lte=now + timedelta(days=max(settings.TASK_REMINDER_DAYS or [1]) + 1)).select_related("assigned_to", "dossier"):
        atteints = paliers_de(tache, now)
        if not atteints:
            continue
        with transaction.atomic():
            nouveaux = [p for p in atteints if claim_once(f"task:{tache.pk}:{p[0]}")]
            if not nouveaux:
                continue
            cle, libelle = nouveaux[-1]
            severite = Notification.Severity.WARNING if cle in {"J-1", "J0"} else Notification.Severity.INFO
            emit([tache.assigned_to], "task_deadline", f"Échéance proche : {libelle}",
                 f"« {tache.title} »{_suffixe_dossier(tache)} — {libelle} (le {_date(tache.due_at)}).",
                 severity=severite, email=True, target_type="task", target_id=tache.pk)
            log_system_event("task_reminder_sent", "task", str(tache.pk), metadata={"palier": cle})
        paliers += 1

    # 3. Retard : l'intéressé et le donneur d'ordre.
    for tache in Task.objects.filter(status__in=OUVERTES, due_at__isnull=False, due_at__lt=now,
                                     overdue_notified_at__isnull=True).select_related("assigned_to", "assigned_by", "dossier"):
        with transaction.atomic():
            if not claim_once(f"task:{tache.pk}:retard"):
                continue
            corps = f"« {tache.title} »{_suffixe_dossier(tache)} devait être terminée le {_date(tache.due_at)}."
            emit([tache.assigned_to], "task_overdue", "Échéance dépassée", corps,
                 severity=Notification.Severity.WARNING, email=True, target_type="task", target_id=tache.pk)
            if tache.assigned_by_id != tache.assigned_to_id:
                emit([tache.assigned_by], "task_overdue", "Échéance dépassée",
                     f"{corps} Assignée à {tache.assigned_to.display_name}.",
                     severity=Notification.Severity.WARNING, email=True, target_type="task", target_id=tache.pk)
            tache.overdue_notified_at = now
            tache.save(update_fields=["overdue_notified_at"])
            log_system_event("task_overdue_notified", "task", str(tache.pk))
        retards += 1

    # 4. Escalade : le retard persiste, le notaire superviseur est saisi.
    delai = timedelta(days=getattr(settings, "TASK_ESCALATION_DAYS", 2))
    for tache in Task.objects.filter(status__in=OUVERTES, due_at__isnull=False, due_at__lt=now - delai,
                                     escalated_at__isnull=True).select_related("assigned_to", "dossier"):
        with transaction.atomic():
            if not claim_once(f"task:{tache.pk}:escalade"):
                continue
            destinataires = _superviseurs(tache)
            emit(destinataires, "task_escalation", "Retard persistant",
                 f"« {tache.title} »{_suffixe_dossier(tache)}, assignée à {tache.assigned_to.display_name}, "
                 f"est en retard depuis le {_date(tache.due_at)}.",
                 severity=Notification.Severity.HIGH, target_type="task", target_id=tache.pk)
            tache.escalated_at = now
            tache.save(update_fields=["escalated_at"])
            log_system_event("task_escalated", "task", str(tache.pk),
                             metadata={"destinataires": [u.pk for u in destinataires]})
        escalades += 1

    affectations = _echeances_affectations(now)
    total = rappels + paliers + retards + escalades + affectations
    return {"items": total, "message": f"rappels : {rappels} | paliers : {paliers} | retards : {retards} | "
                                       f"escalades : {escalades} | affectations : {affectations}"}


def _superviseurs(tache):
    if tache.dossier_id:
        from dossiers.models import DossierAssignment
        sup = [a.user for a in DossierAssignment.objects.filter(
            dossier_id=tache.dossier_id, role=DossierAssignment.Role.SUPERVISOR, user__is_active=True).select_related("user")]
        if sup:
            return sup
    return active_admins()


def _echeances_affectations(now) -> int:
    """Échéance posée sur une affectation de dossier : veille et retard."""
    from dossiers.models import DossierAssignment
    envoyes = 0
    for affectation in DossierAssignment.objects.filter(
            due_at__isnull=False, due_at__lte=now + timedelta(days=1), user__is_active=True,
            dossier__statut__in=["ouvert", "en_instruction", "en_attente_pieces", "pret_pour_acte"],
    ).select_related("dossier", "user", "assigned_by"):
        cle, titre, sev = ("retard", "Échéance d'affectation dépassée", Notification.Severity.WARNING) \
            if affectation.due_at < now else ("J-1", "Échéance d'affectation demain", Notification.Severity.INFO)
        with transaction.atomic():
            if not claim_once(f"assignment:{affectation.pk}:{affectation.due_at.isoformat()}:{cle}"):
                continue
            destinataires = [affectation.user] + ([affectation.assigned_by] if cle == "retard" else [])
            emit(destinataires, "assignment_due", titre,
                 f"Dossier {affectation.dossier.reference} ({affectation.get_role_display()} : "
                 f"{affectation.user.display_name}) — échéance le {_date(affectation.due_at)}.",
                 severity=sev, email=True, target_type="dossier", target_id=affectation.dossier.reference)
            log_system_event("assignment_due_notified", "dossier", affectation.dossier.reference,
                             metadata={"affectation": affectation.pk, "palier": cle})
        envoyes += 1
    return envoyes


@job("rappels_taches", "Rappels d'échéances, retards et escalades", every=300, lock_ttl=600)
def rappels_taches():
    return envoyer_rappels_taches()


@job("envoi_emails", "Envoi des e-mails de notification", every=60, lock_ttl=600, alert_on_failure=True)
def envoi_emails():
    return send_pending_emails()
