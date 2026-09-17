from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def discard_unverifiable_pending_registrations(apps, schema_editor):
    # A pending self-service registration predates the authorization model;
    # it must not survive as an invitation-less activation path.
    apps.get_model("accounts", "PendingRegistration").objects.filter(invite__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [("accounts", "0003_user_google_sub")]

    operations = [
        migrations.AddField(
            model_name="user", name="failed_login_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="user", name="locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="user", name="session_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.CreateModel(
            name="InviteCode",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=96, unique=True)),
                ("email", models.EmailField(max_length=254)),
                ("role", models.CharField(choices=[("admin", "Notaire · Admin"), ("clerc", "Clerc principal"), ("collaborateur", "Collaborateur")], max_length=20)),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_invites", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddField(
            model_name="pendingregistration", name="invite",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="accounts.invitecode"),
        ),
        migrations.AddField(
            model_name="pendingregistration", name="invite_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(discard_unverifiable_pending_registrations, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="pendingregistration", name="invite",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="accounts.invitecode"),
        ),
    ]
