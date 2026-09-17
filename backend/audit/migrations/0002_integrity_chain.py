from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]
    operations = [
        migrations.AlterField(model_name="auditlog", name="timestamp", field=models.DateTimeField(default=django.utils.timezone.now, editable=False)),
        migrations.AddField(model_name="auditlog", name="previous_hash", field=models.CharField(blank=True, max_length=64)),
        migrations.AddField(model_name="auditlog", name="entry_hash", field=models.CharField(blank=True, db_index=True, max_length=64)),
    ]
