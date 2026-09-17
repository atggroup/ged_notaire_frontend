from django.db import models


class CabinetSettings(models.Model):
    cabinet_name = models.CharField(max_length=255, default="Cabinet notarial")
    codification_policy = models.TextField(blank=True)
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class BackupRun(models.Model):
    """An auditable result of a non-destructive backup integrity check."""
    class Status(models.TextChoices):
        SUCCESS = "success", "Réussie"
        FAILURE = "failure", "Échouée"

    status = models.CharField(max_length=16, choices=Status.choices)
    checked_documents = models.PositiveIntegerField(default=0)
    checked_bytes = models.PositiveBigIntegerField(default=0)
    message = models.TextField(blank=True)
    requested_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="backup_runs")
    kind = models.CharField(max_length=24, default="restore_test")
    local_path = models.CharField(max_length=500, blank=True)
    cloud_status = models.CharField(max_length=24, default="not_configured")
    created_at = models.DateTimeField(auto_now_add=True)
