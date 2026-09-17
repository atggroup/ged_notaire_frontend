from django.conf import settings
from django.db import models


class Permission(models.Model):
    class Level(models.TextChoices):
        READ = "lecture", "Lecture"
        EDIT = "edition", "Édition"
        VALIDATE = "validation", "Validation"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="document_permissions")
    document = models.ForeignKey("documents.Document", null=True, blank=True, on_delete=models.CASCADE, related_name="access_permissions")
    dossier = models.ForeignKey("dossiers.Dossier", null=True, blank=True, on_delete=models.CASCADE, related_name="access_permissions")
    access_level = models.CharField(max_length=20, choices=Level.choices, default=Level.READ)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="granted_permissions")
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AccessRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "en_attente", "En attente"
        ACCEPTED = "accepté", "Accepté"
        REFUSED = "refusé", "Refusé"
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_requests")
    document = models.ForeignKey("documents.Document", null=True, blank=True, on_delete=models.CASCADE)
    dossier = models.ForeignKey("dossiers.Dossier", null=True, blank=True, on_delete=models.CASCADE)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    processed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="processed_access_requests")
    created_at = models.DateTimeField(auto_now_add=True)
