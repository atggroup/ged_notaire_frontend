"""Traçabilité et coordination des travaux de fond.

Tout ce qui tourne sans intervention humaine laisse ici une trace lisible par
le notaire : une automatisation qui échoue sans que personne le sache est
précisément ce que ce module doit rendre impossible.
"""
from django.conf import settings
from django.db import models


class JobRun(models.Model):
    """Une exécution d'un travail planifié (ou demandé depuis l'écran)."""

    class Status(models.TextChoices):
        REQUESTED = "demandé", "Demandé"
        RUNNING = "en_cours", "En cours"
        SUCCESS = "succès", "Réussi"
        FAILURE = "échec", "Échoué"

    class Trigger(models.TextChoices):
        SCHEDULED = "planifié", "Planifié"
        MANUAL = "manuel", "Demandé depuis l'écran"
        COMMAND = "commande", "Ligne de commande"

    name = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField(max_length=16, choices=Trigger.choices, default=Trigger.SCHEDULED)
    triggered_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="job_runs")
    requested_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt = models.PositiveSmallIntegerField(default=1)
    items = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    error = models.TextField(blank=True)
    host = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["name", "-requested_at"], name="jobrun_nom_idx"),
            models.Index(fields=["status"], name="jobrun_statut_idx"),
        ]

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at and self.finished_at:
            return round((self.finished_at - self.started_at).total_seconds(), 2)
        return None


class JobLock(models.Model):
    """Verrou à bail : un seul exécutant par travail, même avec plusieurs
    conteneurs. Le bail expire tout seul si l'exécutant meurt en route, ce
    qu'un simple drapeau booléen ne permet pas."""
    name = models.CharField(max_length=96, unique=True)
    holder = models.CharField(max_length=64, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)


class AutomationMark(models.Model):
    """Mémoire des actions « à ne faire qu'une fois » (un rappel J-3, une
    relance de pièce, une alerte). La contrainte d'unicité est ce qui rend
    chaque automatisation idempotente : rejouer un travail ne double rien."""
    key = models.CharField(max_length=190, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)


class ReferenceSequence(models.Model):
    """Compteur verrouillé pour les références métier (DOM-AAAA-NNNNN…).

    Le calcul `count() + 1` produisait deux fois la même référence sous
    création simultanée — une erreur 500 pour les dossiers, un doublon
    silencieux de séquence pour les pièces."""
    scope = models.CharField(max_length=120, unique=True)
    last_value = models.PositiveIntegerField(default=0)


class WorkerHeartbeat(models.Model):
    """Signe de vie de chaque processus `run_worker`."""
    name = models.CharField(max_length=64, unique=True)
    host = models.CharField(max_length=120, blank=True)
    pid = models.PositiveIntegerField(default=0)
    groups = models.CharField(max_length=120, blank=True)
    last_seen = models.DateTimeField()
