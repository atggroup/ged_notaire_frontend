from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("documents", "0003_workflow_versions_quality"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.AlterField(model_name="document", name="statut", field=models.CharField(choices=[("à_indexer", "À indexer"), ("brouillon", "Brouillon"), ("à_contrôler", "À contrôler"), ("en_validation", "En validation"), ("validé", "Validé"), ("clos", "Clos"), ("archivé", "Archivé"), ("rejeté", "Rejeté"), ("à_corriger", "À corriger"), ("annulé", "Annulé"), ("corbeille", "Corbeille"), ("destruction_demandée", "Destruction demandée"), ("destruction_autorisée", "Destruction autorisée")], default="à_indexer", max_length=32)),
        migrations.AddField(model_name="document", name="ocr_status", field=models.CharField(choices=[("en_attente", "En attente"), ("extrait", "Extrait"), ("indisponible", "Indisponible"), ("échec", "Échec")], default="en_attente", max_length=16)),
        migrations.AddField(model_name="document", name="ocr_processed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="ocr_error", field=models.TextField(blank=True)),
        migrations.AddField(model_name="document", name="trashed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="trashed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="trashed_documents", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="document", name="destruction_requested_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="destruction_requested_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="destruction_requests", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="document", name="destruction_authorized_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="destruction_authorized_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="authorized_destructions", to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name="SavedSearch", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=120)), ("filters", models.JSONField(default=dict)),
            ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="saved_searches", to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name="savedsearch", constraint=models.UniqueConstraint(fields=("user", "name"), name="unique_user_saved_search_name")),
    ]
