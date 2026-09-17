from django.db import migrations, models
import django.db.models.deletion


def migrate_primary_clients_to_parties(apps, schema_editor):
    Client = apps.get_model("dossiers", "Client")
    Dossier = apps.get_model("dossiers", "Dossier")
    DossierParty = apps.get_model("dossiers", "DossierParty")
    for dossier in Dossier.objects.exclude(client="").iterator():
        client, _ = Client.objects.get_or_create(
            nom=dossier.client,
            defaults={"reference": f"CLI-HIST-{dossier.pk:08d}"},
        )
        DossierParty.objects.get_or_create(dossier=dossier, client=client, role="client_principal", defaults={"is_primary": True})


class Migration(migrations.Migration):
    dependencies = [("dossiers", "0003_dossier_domaine")]

    operations = [
        migrations.AlterField(
            model_name="dossier", name="niveau_de_confidentialite",
            field=models.CharField(choices=[("Standard", "Ouvert à l'étude"), ("Restreint", "Restreint"), ("Confidentiel", "Confidentiel"), ("Très confidentiel", "Très confidentiel")], default="Restreint", max_length=20),
        ),
        migrations.AlterField(
            model_name="dossier", name="statut",
            field=models.CharField(choices=[("ouvert", "Ouvert"), ("en_instruction", "En instruction"), ("en_attente_pieces", "En attente de pièces"), ("pret_pour_acte", "Prêt pour acte"), ("finalisé", "Finalisé"), ("clos", "Clos"), ("archivé", "Archivé")], default="ouvert", max_length=32),
        ),
        migrations.AddField(model_name="dossier", name="legal_hold", field=models.BooleanField(default=False)),
        migrations.AddField(model_name="dossier", name="legal_hold_reason", field=models.TextField(blank=True)),
        migrations.CreateModel(
            name="Client",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reference", models.CharField(max_length=32, unique=True)),
                ("nom", models.CharField(max_length=255)),
                ("kind", models.CharField(choices=[("personne_physique", "Personne physique"), ("personne_morale", "Personne morale")], default="personne_physique", max_length=24)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("telephone", models.CharField(blank=True, max_length=32)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="DossierParty",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(default="client_principal", max_length=80)),
                ("is_primary", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="dossiers", to="dossiers.client")),
                ("dossier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="parties", to="dossiers.dossier")),
            ],
        ),
        migrations.AddConstraint(model_name="dossierparty", constraint=models.UniqueConstraint(fields=("dossier", "client", "role"), name="unique_dossier_party_role")),
        migrations.RunPython(migrate_primary_clients_to_parties, migrations.RunPython.noop),
    ]
