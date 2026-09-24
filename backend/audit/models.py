from django.conf import settings
from django.db import models
from django.utils import timezone


class AuditLogQuerySet(models.QuerySet):
    """`save()`/`delete()` du modèle ne sont pas appelés par les opérations
    ensemblistes : sans ce garde-fou, un `AuditLog.objects.filter(...).delete()`
    contournait silencieusement le caractère append-only du journal."""

    def update(self, *args, **kwargs):
        raise RuntimeError("Le journal d'audit est append-only et ne peut pas être modifié.")

    def delete(self, *args, **kwargs):
        raise RuntimeError("Le journal d'audit est append-only et ne peut pas être supprimé.")


class AuditLog(models.Model):
    # PROTECT et non SET_NULL : l'identifiant de l'auteur entre dans le calcul
    # du hash de chaque entrée. Effacer un compte remettrait `user_id` à NULL
    # et invaliderait rétroactivement tout le chaînage. Un départ se gère par
    # désactivation (`is_active`, `deactivated_at`), jamais par suppression.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="audit_logs")
    action = models.CharField(max_length=100)
    target_type = models.CharField(max_length=50, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    timestamp = models.DateTimeField(default=timezone.now, editable=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    result = models.CharField(max_length=20, default="success")
    metadata = models.JSONField(default=dict, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True)
    entry_hash = models.CharField(max_length=64, blank=True, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["-timestamp"]),
            models.Index(fields=["action", "-timestamp"], name="audit_action_idx"),
            models.Index(fields=["user", "-timestamp"], name="audit_acteur_idx"),
            models.Index(fields=["target_id"], name="audit_cible_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Le journal d'audit est append-only et ne peut pas être modifié.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Le journal d'audit est append-only et ne peut pas être supprimé.")


class SecurityAlert(models.Model):
    """Alerte levée par la surveillance automatique.

    Une alerte ne bloque rien d'elle-même (hors verrouillage de compte déjà
    existant) : elle est examinée puis marquée « traitée » par un notaire,
    avec un commentaire — c'est ce geste qui est tracé."""

    class Severity(models.TextChoices):
        MEDIUM = "moyenne", "Moyenne"
        HIGH = "haute", "Haute"
        CRITICAL = "critique", "Critique"

    class Status(models.TextChoices):
        OPEN = "ouverte", "Ouverte"
        HANDLED = "traitée", "Traitée"

    rule = models.CharField(max_length=40)
    severity = models.CharField(max_length=10, choices=Severity.choices)
    title = models.CharField(max_length=255)
    message = models.TextField()
    subject_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="security_alerts")
    dedup_key = models.CharField(max_length=190, unique=True)
    details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="handled_security_alerts")
    handled_at = models.DateTimeField(null=True, blank=True)
    handling_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="alerte_statut_idx")]
