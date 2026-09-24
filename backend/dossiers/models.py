from django.conf import settings
from django.db import models
from ged_backend.referentiels import DOMAINES


class Dossier(models.Model):
    class Confidentiality(models.TextChoices):
        STANDARD = "Standard", "Ouvert à l'étude"
        RESTRICTED = "Restreint", "Restreint"
        CONFIDENTIAL = "Confidentiel", "Confidentiel"
        VERY_CONFIDENTIAL = "Très confidentiel", "Très confidentiel"

    class Status(models.TextChoices):
        OPEN = "ouvert", "Ouvert"
        IN_PROGRESS = "en_instruction", "En instruction"
        WAITING = "en_attente_pieces", "En attente de pièces"
        READY = "pret_pour_acte", "Prêt pour acte"
        FINALIZED = "finalisé", "Finalisé"
        CLOSED = "clos", "Clos"
        ARCHIVED = "archivé", "Archivé"

    reference = models.CharField(max_length=64, unique=True)
    domaine = models.CharField(max_length=4, choices=DOMAINES, default="AUT")
    nom = models.CharField(max_length=255)
    client = models.CharField(max_length=255, blank=True)
    objet = models.CharField(max_length=255, blank=True)
    statut = models.CharField(max_length=32, choices=Status.choices, default=Status.OPEN)
    # Allows each scan to find the single dedicated folder for a client.
    client_key = models.CharField(max_length=96, blank=True, db_index=True)
    niveau_de_confidentialite = models.CharField(max_length=20, choices=Confidentiality.choices, default=Confidentiality.RESTRICTED)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_dossiers")
    created_at = models.DateTimeField(auto_now_add=True)
    legal_hold = models.BooleanField(default=False)
    legal_hold_reason = models.TextField(blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.reference} — {self.nom}"


class Client(models.Model):
    """A person or organisation can participate in several distinct cases."""
    class Kind(models.TextChoices):
        PERSON = "personne_physique", "Personne physique"
        COMPANY = "personne_morale", "Personne morale"

    reference = models.CharField(max_length=32, unique=True)
    nom = models.CharField(max_length=255)
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.PERSON)
    email = models.EmailField(blank=True)
    telephone = models.CharField(max_length=32, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class DossierParty(models.Model):
    dossier = models.ForeignKey(Dossier, on_delete=models.CASCADE, related_name="parties")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="dossiers")
    role = models.CharField(max_length=80, default="client_principal")
    relationship = models.CharField(max_length=120, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dossier", "client", "role"], name="unique_dossier_party_role")]


class DossierAssignment(models.Model):
    class Role(models.TextChoices):
        CLERK = "clerc_responsable", "Clerc responsable"
        COLLABORATOR = "collaborateur", "Collaborateur affecté"
        SUPERVISOR = "notaire_superviseur", "Notaire superviseur"

    dossier = models.ForeignKey(Dossier, on_delete=models.CASCADE, related_name="assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dossier_assignments")
    role = models.CharField(max_length=32, choices=Role.choices)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_dossier_assignments")
    assigned_at = models.DateTimeField(auto_now_add=True)
    due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dossier", "user", "role"], name="unique_dossier_assignment")]


class ChecklistTemplate(models.Model):
    """Pièce attendue pour un type d'affaire (domaine).

    Le contenu relève du notaire : la GED fournit un jeu de propositions
    (`python manage.py charger_modeles_checklist`) qu'il ajuste, et c'est lui
    qui décide de ce qui est obligatoire."""
    domaine = models.CharField(max_length=4, choices=DOMAINES)
    label = models.CharField(max_length=255)
    # Type documentaire qui satisfait l'élément : un dépôt de ce type dans le
    # dossier y est rattaché automatiquement (« reçu, à vérifier »).
    type_code = models.CharField(max_length=6, blank=True)
    required = models.BooleanField(default=True)
    # Jours après l'ouverture du dossier avant relance si la pièce manque
    # (vide = délai par défaut CHECKLIST_REMINDER_DAYS).
    reminder_days = models.PositiveSmallIntegerField(null=True, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["domaine", "order", "label"]
        constraints = [models.UniqueConstraint(fields=["domaine", "label"], name="unique_modele_checklist")]

    def __str__(self):
        return f"{self.domaine} — {self.label}"


class DossierChecklistItem(models.Model):
    class Source(models.TextChoices):
        MANUAL = "manuel", "Ajouté à la main"
        TEMPLATE = "modele", "Généré depuis le modèle"

    dossier = models.ForeignKey(Dossier, on_delete=models.CASCADE, related_name="checklist_items")
    label = models.CharField(max_length=255)
    required = models.BooleanField(default=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="completed_dossier_checklist_items")
    created_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=8, choices=Source.choices, default=Source.MANUAL)
    template = models.ForeignKey(ChecklistTemplate, null=True, blank=True, on_delete=models.SET_NULL, related_name="items")
    type_code = models.CharField(max_length=6, blank=True)
    reminder_days = models.PositiveSmallIntegerField(null=True, blank=True)
    # Pièce déposée qui répond à l'élément. Sa présence ne vaut PAS
    # complétude : cocher reste un geste humain, après contrôle.
    document = models.ForeignKey("documents.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="checklist_items")
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["dossier", "template"], name="unique_item_par_modele")]

    @property
    def state(self) -> str:
        if self.completed_at:
            return "complet"
        if self.document_id:
            return "reçu_à_vérifier"
        return "attendu"


class PhysicalArchiveRecord(models.Model):
    """Location and circulation of the paper original corresponding to a case.
    The record is kept even when the original is checked back in."""
    dossier = models.ForeignKey(Dossier, on_delete=models.CASCADE, related_name="physical_records")
    document = models.ForeignKey("documents.Document", null=True, blank=True, on_delete=models.SET_NULL, related_name="physical_records")
    room = models.CharField(max_length=80)
    cabinet = models.CharField(max_length=80)
    shelf = models.CharField(max_length=80)
    box = models.CharField(max_length=80)
    folder = models.CharField(max_length=80)
    checked_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="checked_out_physical_records")
    checked_out_at = models.DateTimeField(null=True, blank=True)
    checkout_reason = models.TextField(blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
