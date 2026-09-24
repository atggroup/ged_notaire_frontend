from django.conf import settings
from django.db import models


class Notification(models.Model):
    class Severity(models.TextChoices):
        INFO = "info", "Information"
        WARNING = "attention", "Attention"
        HIGH = "haute", "Haute"
        CRITICAL = "critique", "Critique"

    class EmailStatus(models.TextChoices):
        NONE = "", "Aucun e-mail"
        PENDING = "en_attente", "En attente d'envoi"
        SENT = "envoyé", "Envoyé"
        FAILED = "échec", "Échec définitif"

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=50)
    message = models.TextField()
    title = models.CharField(max_length=255, blank=True)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    severity = models.CharField(max_length=12, choices=Severity.choices, default=Severity.INFO)
    # Cible de la notification (lien cliquable dans la cloche).
    target_type = models.CharField(max_length=30, blank=True)
    target_id = models.CharField(max_length=80, blank=True)
    # Doublure e-mail, envoyée par le travail `envoi_emails` avec relances :
    # une boîte injoignable ne fait plus perdre le message.
    email_status = models.CharField(max_length=12, choices=EmailStatus.choices, default=EmailStatus.NONE, blank=True)
    email_attempts = models.PositiveSmallIntegerField(default=0)
    email_error = models.TextField(blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    next_email_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # La cloche interroge « non lues de cet utilisateur » à chaque page.
        indexes = [
            models.Index(fields=["recipient", "read"], name="notif_cloche_idx"),
            models.Index(fields=["email_status", "next_email_at"], name="notif_email_idx"),
        ]


class Task(models.Model):
    class Status(models.TextChoices):
        OPEN = "ouverte", "Ouverte"
        IN_PROGRESS = "en_cours", "En cours"
        DONE = "terminée", "Terminée"
        CANCELLED = "annulée", "Annulée"

    class Priority(models.TextChoices):
        LOW = "basse", "Basse"
        NORMAL = "normale", "Normale"
        HIGH = "haute", "Haute"
        URGENT = "urgente", "Urgente"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasks")
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_tasks")
    dossier = models.ForeignKey("dossiers.Dossier", null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks")
    due_at = models.DateTimeField(null=True, blank=True)
    reminder_at = models.DateTimeField(null=True, blank=True)
    priority = models.CharField(max_length=12, choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Horodate l'envoi du rappel : sans cette mémoire, la commande périodique
    # `envoyer_rappels` renverrait le même rappel à chaque passage.
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    overdue_notified_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)

    class Source(models.TextChoices):
        MANUAL = "manuelle", "Créée par un utilisateur"
        AUTO = "automatique", "Créée par la GED"

    source = models.CharField(max_length=12, choices=Source.choices, default=Source.MANUAL)
    # Identité de la règle qui a créé la tâche (ex. « checklist:42 ») : une
    # tâche automatique n'est jamais créée deux fois pour la même cause.
    auto_key = models.CharField(max_length=120, null=True, blank=True, unique=True)

    class Meta:
        indexes = [
            models.Index(fields=["assigned_to", "status"], name="tache_par_personne_idx"),
            models.Index(fields=["reminder_at"], name="tache_rappel_idx"),
            models.Index(fields=["due_at"], name="tache_echeance_idx"),
        ]
