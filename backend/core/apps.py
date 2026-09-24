from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Socle des automatisations : exécutions de travaux, verrous, séquences."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Automatisations"
