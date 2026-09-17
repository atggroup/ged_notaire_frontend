from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_add_login_mfa_otp_purpose"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="can_scan",
            field=models.BooleanField(default=False),
        ),
    ]
