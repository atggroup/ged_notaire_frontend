import hashlib
import os
from django.conf import settings
from django.db import models
from django.utils import timezone
from ged_backend.referentiels import NATURES, ORIGINES, QUALITES_PARTIES, SUPPORTS, TYPES_DOCUMENTS
from .crypto import decrypt


def document_upload_path(instance, filename: str) -> str:
    # Le nom d'origine (« Testament_Kouassi.pdf ») n'entre plus dans le chemin
    # de stockage : il se lisait en clair sur le disque et dans chaque copie de
    # sauvegarde, alors que le contenu, lui, est chiffré. Il reste conservé en
    # base (`original_filename`). Les fichiers déjà déposés gardent leur chemin.
    year = timezone.now().strftime("%Y")
    folder = instance.dossier.reference if instance.dossier else "sans-dossier"
    extension = os.path.splitext(os.path.basename(filename))[1].lower()[:8]
    return f"documents/{folder}/{year}/{instance.reference}{extension}.enc"


class Document(models.Model):
    class Confidentiality(models.TextChoices):
        STANDARD = "Standard", "Standard"
        RESTRICTED = "Restreint", "Restreint"
        CONFIDENTIAL = "Confidentiel", "Confidentiel"
        VERY_CONFIDENTIAL = "Très confidentiel", "Très confidentiel"
    # Attention : ce workflow est distinct du référentiel documentaire
    # `ged_backend.referentiels.STATUTS` (18 codes du cadrage, exposés tels
    # quels par /api/referentiels à titre de nomenclature). C'est CETTE
    # énumération qui pilote le cycle de vie applicatif ; les deux
    # vocabulaires ne doivent pas être confondus.
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
        PROCESSING = "en_cours", "En cours"
        EXTRACTED = "extrait", "Extrait"
        UNAVAILABLE = "indisponible", "Indisponible"
        FAILED = "échec", "Échec"

    ocr_status = models.CharField(max_length=16, choices=OCRStatus.choices, default=OCRStatus.PENDING)
    ocr_processed_at = models.DateTimeField(null=True, blank=True)
    ocr_error = models.TextField(blank=True)
    # File OCR : tentatives, prise en charge (détection d'un traitement
    # bloqué) et prochaine tentative après un échec transitoire.
    ocr_attempts = models.PositiveSmallIntegerField(default=0)
    ocr_started_at = models.DateTimeField(null=True, blank=True)
    ocr_next_retry_at = models.DateTimeField(null=True, blank=True)
    # Date de fin de validité d'une pièce datée (pièce d'identité, certificat
    # d'urbanisme, état hypothécaire…) : l'expiration est signalée à l'avance.
    valid_until = models.DateField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_documents")
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="validated_documents")
    validated_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)
    trashed_at = models.DateTimeField(null=True, blank=True)
    # Mémorise le statut qui précède la mise à la corbeille : sans lui, une
    # restauration ne peut pas tenir la promesse faite à l'écran (« le document
    # retrouve son statut précédent ») et un acte validé revenait « à_indexer »
    # tout en conservant sa signature notariale.
    statut_avant_corbeille = models.CharField(max_length=32, blank=True)
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
        indexes = [
            models.Index(fields=["type", "niveau_de_confidentialite"]),
            models.Index(fields=["reference"]),
            # L'ordre par défaut n'était couvert par aucun index : tout
            # affichage de liste passait par un tri complet de la table.
            models.Index(fields=["-created_at"]),
            # File de numérisation et compteurs du tableau de bord.
            models.Index(fields=["statut"]),
            # Liste des documents : is_archived + trashed_at + is_current.
            models.Index(fields=["is_archived", "is_current", "trashed_at"], name="doc_liste_courante_idx"),
            # Onglet « Documents » d'un dossier.
            models.Index(fields=["dossier", "is_current"], name="doc_par_dossier_idx"),
            models.Index(fields=["ocr_status", "ocr_next_retry_at"], name="doc_file_ocr_idx"),
        ]

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
