from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_invitation_and_session_security")]
    operations = [
        migrations.AlterField(
            model_name="otpcode",
            name="purpose",
            field=models.CharField(choices=[("register", "Register"), ("forgot_password", "Forgot password"), ("login_mfa", "Login MFA")], max_length=32),
        ),
    ]
