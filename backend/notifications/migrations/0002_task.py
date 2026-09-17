from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dossiers", "0004_parties_and_legal_hold"), ("notifications", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="Task",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)), ("description", models.TextField(blank=True)),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("priority", models.CharField(choices=[("basse", "Basse"), ("normale", "Normale"), ("haute", "Haute"), ("urgente", "Urgente")], default="normale", max_length=12)),
                ("status", models.CharField(choices=[("ouverte", "Ouverte"), ("en_cours", "En cours"), ("terminée", "Terminée"), ("annulée", "Annulée")], default="ouverte", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)), ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("assigned_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assigned_tasks", to=settings.AUTH_USER_MODEL)),
                ("assigned_to", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tasks", to=settings.AUTH_USER_MODEL)),
                ("dossier", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tasks", to="dossiers.dossier")),
            ],
        ),
    ]
