import hashlib
import os
from django.conf import settings
from django.db import models
from django.utils import timezone
from ged_backend.referentiels import NATURES, ORIGINES, QUALITES_PARTIES, STATUT_LABELS, SUPPORTS, TYPES_DOCUMENTS
from .crypto import decrypt


def document_upload_path(instance, filename: str) -> str:
    year = timezone.now().strftime("%Y")
    folder = instance.dossier.reference if instance.dossier else "sans-dossier"
    return f"documents/{folder}/{year}/{instance.reference}-{os.path.basename(filename)}.enc"


class Document(models.Model):
    class Confidentiality(models.TextChoices):
        STANDARD = "Standard", "Standard"
        RESTRICTED = "Restreint", "Restreint"
        CONFIDENTIAL = "Confidentiel", "Confidentiel"
        VERY_CONFIDENTIAL = "Très confidentiel", "Très confidentiel"
    class Status(models.TextChoices):
        TO_INDEX = "à_indexer", "À indexer"
        DRAFT = "brouillon", "Brouillon"
        TO_CONTROL = "à_contrôler", "À contrôler"
        PENDING = "en_validation", "En validation"
        VALIDATED = "validé", "Validé"
        CLOSED = "clos", "Clos"
        ARCHIVED = "archivé", "Archivé"
        REJECTED = "rejeté", "Rejeté"
        TO_FIX = "à_corriger", "À corriger"
        CANCELLED = "annulé", "Annulé"
        TRASHED = "corbeille", "Corbeille"
        DESTRUCTION_REQUESTED = "destruction_demandée", "Destruction demandée"
        DESTRUCTION_AUTHORIZED = "destruction_autorisée", "Destruction autorisée"
        DESTROYED = "détruit", "Détruit"

    reference = models.CharField(max_length=80, unique=True)
    code_notarial = models.CharField(max_length=150, blank=True)
    sequence = models.PositiveIntegerField(default=0)
    dossier = models.ForeignKey("dossiers.Dossier", null=True, blank=True, on_delete=models.SET_NULL, related_name="documents")
    type = models.CharField(max_length=100)
    type_code = models.CharField(max_length=6, choices=TYPES_DOCUMENTS, blank=True)
    origine = models.CharField(max_length=4, choices=ORIGINES, default="CAB")
    support = models.CharField(max_length=4, choices=SUPPORTS, default="SCN")
    nature = models.CharField(max_length=4, choices=NATURES, default="SCN")
    qualite_partie = models.CharField(max_length=6, choices=QUALITES_PARTIES, blank=True)
    nom = models.CharField(max_length=255)
    fichier = models.FileField(upload_to=document_upload_path)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    # Which entry of the encryption keyring produced this ciphertext — lets
    # the active key rotate without ever breaking previously stored files.
    encryption_key_id = models.CharField(max_length=64, blank=True)
    extracted_text = models.TextField(blank=True)
    niveau_de_confidentialite = models.CharField(max_length=20, choices=Confidentiality.choices, default=Confidentiality.STANDARD)
    statut = models.CharField(max_length=32, choices=Status.choices, default=Status.TO_INDEX)
    version = models.PositiveIntegerField(default=1)
    master_reference = models.CharField(max_length=80, db_index=True, blank=True)
    previous_version = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="next_versions")
    is_current = models.BooleanField(default=True)
    quality_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quality_checked_documents")
    quality_checked_at = models.DateTimeField(null=True, blank=True)
    quality_passed = models.BooleanField(default=False)
    quality_notes = models.TextField(blank=True)
    class OCRStatus(models.TextChoices):
        PENDING = "en_attente", "En attente"
        EXTRACTED = "extrait", "Extrait"
        UNAVAILABLE = "indisponible", "Indisponible"
        FAILED = "échec", "Échec"

    ocr_status = models.CharField(max_length=16, choices=OCRStatus.choices, default=OCRStatus.PENDING)
    ocr_processed_at = models.DateTimeField(null=True, blank=True)
    ocr_error = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_documents")
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="validated_documents")
    validated_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)
    trashed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="trashed_documents")
    destruction_requested_at = models.DateTimeField(null=True, blank=True)
    destruction_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="destruction_requests")
    destruction_authorized_at = models.DateTimeField(null=True, blank=True)
    destruction_authorized_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="authorized_destructions")
    destroyed_at = models.DateTimeField(null=True, blank=True)
    destroyed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="destroyed_documents")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["type", "niveau_de_confidentialite"]), models.Index(fields=["reference"])]

    def decrypted_bytes(self) -> bytes:
        with self.fichier.open("rb") as source:
            return decrypt(source.read(), self.encryption_key_id or None)

    @property
    def id_maitre(self) -> str:
        """Stable technical identity shared by every version."""
        return self.master_reference or self.reference

    def code_notarial_actuel(self) -> str:
        """Human reference. Status is deliberately never part of its value."""
        if not self.dossier_id or not self.type_code:
            return self.code_notarial
        parts = self.dossier.reference.split("-")
        dom, annee, num_dossier = (parts + ["AUT", "0000", "00000"])[:3]
        return f"NOT-{dom}-{annee}-{num_dossier}-{self.type_code}-{self.sequence:03d}-V{self.version:02d}"


class DocumentFavorite(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="favorites")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "document"], name="unique_user_document_favorite")]


class SavedSearch(models.Model):
    """A named, personal multi-criteria view; query values are deliberately
    stored as data, never as executable query fragments."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_searches")
    name = models.CharField(max_length=120)
    filters = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="unique_user_saved_search_name")]
