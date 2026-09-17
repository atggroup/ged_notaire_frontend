from django.db import migrations, models
class Migration(migrations.Migration):
    dependencies=[("dossiers","0005_operations_and_physical_archive")]
    operations=[migrations.AddField(model_name="dossierparty", name="relationship", field=models.CharField(blank=True,max_length=120))]
