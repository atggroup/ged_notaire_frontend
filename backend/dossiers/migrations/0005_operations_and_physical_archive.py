from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("dossiers", "0004_parties_and_legal_hold"), ("documents", "0004_ocr_trash_saved_search"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name="DossierAssignment", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("role", models.CharField(choices=[("clerc_responsable", "Clerc responsable"), ("collaborateur", "Collaborateur affecté"), ("notaire_superviseur", "Notaire superviseur")], max_length=32)),
            ("assigned_at", models.DateTimeField(auto_now_add=True)), ("due_at", models.DateTimeField(blank=True, null=True)),
            ("assigned_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assigned_dossier_assignments", to=settings.AUTH_USER_MODEL)),
            ("dossier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignments", to="dossiers.dossier")),
            ("user", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="dossier_assignments", to=settings.AUTH_USER_MODEL)),
        ]),
        migrations.AddConstraint(model_name="dossierassignment", constraint=models.UniqueConstraint(fields=("dossier", "user", "role"), name="unique_dossier_assignment")),
        migrations.CreateModel(name="DossierChecklistItem", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("label", models.CharField(max_length=255)), ("required", models.BooleanField(default=True)),
            ("completed_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("completed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="completed_dossier_checklist_items", to=settings.AUTH_USER_MODEL)),
            ("dossier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="checklist_items", to="dossiers.dossier")),
        ]),
        migrations.CreateModel(name="PhysicalArchiveRecord", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("room", models.CharField(max_length=80)), ("cabinet", models.CharField(max_length=80)), ("shelf", models.CharField(max_length=80)), ("box", models.CharField(max_length=80)), ("folder", models.CharField(max_length=80)),
            ("checked_out_at", models.DateTimeField(blank=True, null=True)), ("checkout_reason", models.TextField(blank=True)), ("returned_at", models.DateTimeField(blank=True, null=True)), ("created_at", models.DateTimeField(auto_now_add=True)),
            ("checked_out_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="checked_out_physical_records", to=settings.AUTH_USER_MODEL)),
            ("document", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="physical_records", to="documents.document")),
            ("dossier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="physical_records", to="dossiers.dossier")),
        ]),
    ]
