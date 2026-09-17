from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("notifications", "0002_task")]
    operations = [migrations.AddField(model_name="task", name="reminder_at", field=models.DateTimeField(blank=True, null=True))]
