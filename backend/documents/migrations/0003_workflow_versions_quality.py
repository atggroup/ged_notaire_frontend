from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preserve_existing_document_history(apps, schema_editor):
    Document = apps.get_model("documents", "Document")
    Document.objects.filter(statut="en_attente_validation").update(statut="en_validation")
    Document.objects.filter(statut="validé").update(quality_passed=True)
    for document in Document.objects.filter(master_reference="").iterator():
        document.master_reference = document.reference
        document.save(update_fields=["master_reference"])


class Migration(migrations.Migration):
    dependencies = [("documents", "0002_document_referentiels")]

    operations = [
        migrations.AlterField(
            model_name="document", name="niveau_de_confidentialite",
            field=models.CharField(choices=[("Standard", "Standard"), ("Restreint", "Restreint"), ("Confidentiel", "Confidentiel"), ("Très confidentiel", "Très confidentiel")], default="Standard", max_length=20),
        ),
        migrations.AlterField(
            model_name="document", name="statut",
            field=models.CharField(choices=[("à_indexer", "À indexer"), ("brouillon", "Brouillon"), ("à_contrôler", "À contrôler"), ("en_validation", "En validation"), ("validé", "Validé"), ("clos", "Clos"), ("archivé", "Archivé"), ("rejeté", "Rejeté"), ("à_corriger", "À corriger"), ("annulé", "Annulé")], default="à_indexer", max_length=32),
        ),
        migrations.AddField(model_name="document", name="master_reference", field=models.CharField(blank=True, db_index=True, max_length=80)),
        migrations.AddField(model_name="document", name="is_current", field=models.BooleanField(default=True)),
        migrations.AddField(model_name="document", name="quality_checked_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="quality_passed", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="document", name="quality_notes", field=models.TextField(blank=True)),
        migrations.AddField(model_name="document", name="previous_version", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="next_versions", to="documents.document")),
        migrations.AddField(model_name="document", name="quality_checked_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="quality_checked_documents", to=settings.AUTH_USER_MODEL)),
        migrations.RunPython(preserve_existing_document_history, migrations.RunPython.noop),
    ]
