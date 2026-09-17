from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
class Migration(migrations.Migration):
    dependencies=[("accounts","0006_user_can_scan")]
    operations=[
      migrations.AddField(model_name="user",name="deactivated_at",field=models.DateTimeField(blank=True,null=True)),
      migrations.AddField(model_name="user",name="departure_reason",field=models.TextField(blank=True)),
      migrations.AddField(model_name="user",name="deactivated_by",field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name="deactivated_users",to=settings.AUTH_USER_MODEL)),
    ]
