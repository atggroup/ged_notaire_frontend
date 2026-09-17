from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settings_app", "0002_backuprun")]
    operations = [
        migrations.AddField(model_name="backuprun", name="kind", field=models.CharField(default="restore_test", max_length=24)),
        migrations.AddField(model_name="backuprun", name="local_path", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="backuprun", name="cloud_status", field=models.CharField(default="not_configured", max_length=24)),
    ]
