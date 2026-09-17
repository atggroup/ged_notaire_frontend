from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("documents", "0005_document_encryption_key_id")]
    operations = [
        migrations.AddField(model_name="document", name="destroyed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="document", name="destroyed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="destroyed_documents", to=settings.AUTH_USER_MODEL)),
    ]
