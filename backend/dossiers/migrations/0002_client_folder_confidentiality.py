# Generated manually to keep deployments reproducible without relying on a local virtualenv.
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("dossiers", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="dossier", name="client_key",
            field=models.CharField(blank=True, db_index=True, max_length=96),
        ),
        migrations.AddField(
            model_name="dossier", name="niveau_de_confidentialite",
            field=models.CharField(
                choices=[("Standard", "Ouvert à l'étude"), ("Restreint", "Restreint"), ("Confidentiel", "Confidentiel")],
                default="Restreint", max_length=20,
            ),
        ),
    ]
