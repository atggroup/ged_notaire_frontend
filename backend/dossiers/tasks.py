"""Suivi quotidien des dossiers ouverts.

- pièces obligatoires non reçues après le délai → tâche de relance
  automatique (une seule par élément) + notification au clerc responsable ;
- pièces datées qui expirent bientôt ou ont expiré → alerte (et tâche à
  l'expiration) ;
- originaux papier sortis depuis trop longtemps → rappel de retour.

Rien n'est coché, validé ni déplacé : ces travaux signalent, l'humain décide.
"""
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.services import log_system_event
from core.coordination import claim_once
from core.jobs import job

from .models import Dossier, DossierAssignment, DossierChecklistItem, PhysicalArchiveRecord

DOSSIERS_OUVERTS = [Dossier.Status.OPEN, Dossier.Status.IN_PROGRESS, Dossier.Status.WAITING, Dossier.Status.READY]


def responsable(dossier):
    """Clerc responsable du dossier, à défaut son créateur (s'il est actif)."""
    affectation = DossierAssignment.objects.filter(dossier=dossier, role=DossierAssignment.Role.CLERK,
                                                   user__is_active=True).select_related("user").first()
    if affectation:
        return affectation.user
    if dossier.created_by.is_active:
        return dossier.created_by
    from notifications.services import active_admins
    admins = active_admins()
    return admins[0] if admins else None


def _creer_tache(auto_key, titre, description, assignee, dossier, echeance, priorite="haute"):
    from notifications.models import Task
    try:
        with transaction.atomic():
            return Task.objects.create(
                title=titre[:255], description=description, assigned_to=assignee, assigned_by=dossier.created_by,
                dossier=dossier, due_at=echeance, priority=priorite, source=Task.Source.AUTO, auto_key=auto_key)
    except IntegrityError:
        return None  # déjà créée par un passage précédent


def relancer_pieces_manquantes(now=None) -> dict:
    from notifications.models import Notification
    from notifications.services import emit
    now = now or timezone.now()
    defaut = getattr(settings, "CHECKLIST_REMINDER_DAYS", 7)
    manquants = (DossierChecklistItem.objects
                 .filter(required=True, completed_at__isnull=True, document__isnull=True,
                         dossier__statut__in=DOSSIERS_OUVERTS, dossier__legal_hold=False)
                 .select_related("dossier", "dossier__created_by"))
    par_dossier = defaultdict(list)
    for item in manquants:
        delai = item.reminder_days if item.reminder_days is not None else defaut
        if item.created_at > now - timedelta(days=delai):
            continue
        par_dossier[item.dossier].append(item)
    taches = 0
    for dossier, items in par_dossier.items():
        assignee = responsable(dossier)
        if assignee is None:
            continue
        nouveaux = []
        with transaction.atomic():
            for item in items:
                if not claim_once(f"checklist:{item.pk}:relance"):
                    continue
                tache = _creer_tache(f"checklist:{item.pk}:{timezone.localdate(now):%Y%m%d}",f"Relancer : {item.label}",
                                     f"Pièce obligatoire non reçue pour le dossier {dossier.reference}. "
                                     "Tâche créée automatiquement par la GED.",
                                     assignee, dossier, now + timedelta(days=7))
                nouveaux.append(item)
                taches += 1 if tache else 0
            if not nouveaux:
                continue
            libelles = ", ".join(i.label for i in nouveaux[:5]) + ("…" if len(nouveaux) > 5 else "")
            emit([assignee], "missing_documents", "Pièces manquantes",
                 f"Dossier {dossier.reference} : {len(nouveaux)} pièce(s) obligatoire(s) toujours attendue(s) — {libelles}. "
                 "Une tâche de relance a été créée.", severity=Notification.Severity.WARNING, email=True,
                 target_type="dossier", target_id=dossier.reference)
            log_system_event("missing_documents_detected", "dossier", dossier.reference,
                             metadata={"elements": [i.pk for i in nouveaux], "responsable": assignee.pk})
    return taches


def signaler_expirations(now=None) -> int:
    from documents.models import Document
    from notifications.models import Notification
    from notifications.services import emit
    now = now or timezone.now()
    aujourd_hui = timezone.localdate(now)
    preavis = aujourd_hui + timedelta(days=getattr(settings, "DOCUMENT_EXPIRY_NOTICE_DAYS", 30))
    signales = 0
    pieces = (Document.objects.filter(valid_until__isnull=False, valid_until__lte=preavis, is_current=True,
                                      trashed_at__isnull=True, is_archived=False, dossier__statut__in=DOSSIERS_OUVERTS)
              .exclude(statut__in=[Document.Status.DESTROYED, Document.Status.CANCELLED, Document.Status.REJECTED])
              .select_related("dossier", "dossier__created_by", "uploaded_by"))
    for doc in pieces:
        expiree = doc.valid_until < aujourd_hui
        cle = f"doc:{doc.pk}:{doc.valid_until.isoformat()}:{'expiree' if expiree else 'preavis'}"
        assignee = responsable(doc.dossier)
        with transaction.atomic():
            if not claim_once(cle):
                continue
            quand = f"a expiré le {doc.valid_until:%d/%m/%Y}" if expiree else f"expire le {doc.valid_until:%d/%m/%Y}"
            emit([assignee, doc.uploaded_by], "document_expiry", "Pièce expirée" if expiree else "Pièce bientôt expirée",
                 f"La pièce {doc.reference} ({doc.type}) du dossier {doc.dossier.reference} {quand}. "
                 "Demandez une pièce à jour au client.",
                 severity=Notification.Severity.WARNING if expiree else Notification.Severity.INFO, email=expiree,
                 target_type="document", target_id=doc.reference)
            if expiree and assignee:
                _creer_tache(f"expiry:{doc.pk}:{doc.valid_until.isoformat()}", f"Pièce expirée : {doc.type}",
                             f"La pièce {doc.reference} du dossier {doc.dossier.reference} {quand}. "
                             "Obtenir une pièce à jour. Tâche créée automatiquement par la GED.",
                             assignee, doc.dossier, now + timedelta(days=7))
            log_system_event("document_expiry_notified", "document", doc.reference,
                             metadata={"valid_until": doc.valid_until.isoformat(), "expiree": expiree})
        signales += 1
    return signales


def rappeler_retours_originaux(now=None) -> int:
    from notifications.services import active_admins, emit
    now = now or timezone.now()
    delai = timedelta(days=getattr(settings, "PHYSICAL_RETURN_DAYS", 14))
    semaine = timezone.localdate(now).isocalendar()
    rappels = 0
    for record in PhysicalArchiveRecord.objects.filter(checked_out_at__lt=now - delai, returned_at__isnull=True).select_related("dossier", "checked_out_by"):
        with transaction.atomic():
            # Au plus un rappel par semaine tant que l'original n'est pas revenu.
            if not claim_once(f"physical:{record.pk}:{record.checked_out_at:%Y%m%d}:{semaine[0]}-{semaine[1]}"):
                continue
            emit([record.checked_out_by, *active_admins()], "physical_return", "Original à réintégrer",
                 f"L'original du dossier {record.dossier.reference} (boîte {record.box}, chemise {record.folder}) est sorti "
                 f"depuis le {timezone.localtime(record.checked_out_at):%d/%m/%Y}. Motif : {record.checkout_reason[:120]}",
                 target_type="dossier", target_id=record.dossier.reference)
            log_system_event("physical_return_reminder", "dossier", record.dossier.reference, metadata={"record": record.pk})
        rappels += 1
    return rappels


@job("suivi_dossiers", "Pièces manquantes, pièces expirées et originaux sortis", daily_at="07:00", retries=2, retry_delay=1800)
def suivi_dossiers():
    taches = relancer_pieces_manquantes()
    expirations = signaler_expirations()
    retours = rappeler_retours_originaux()
    return {"items": taches + expirations + retours,
            "message": f"relances de pièces : {taches} | pièces datées signalées : {expirations} | originaux à réintégrer : {retours}"}
