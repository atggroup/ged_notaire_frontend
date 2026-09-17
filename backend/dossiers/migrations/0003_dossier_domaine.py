from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dossiers', '0002_client_folder_confidentiality'),
    ]

    operations = [
        migrations.AddField(
            model_name='dossier',
            name='domaine',
            field=models.CharField(choices=[('IMM', 'Immobilier'), ('FON', 'Foncier'), ('SUC', 'Succession'), ('DON', 'Donation'), ('FAM', 'Famille'), ('MAT', 'Régime matrimonial'), ('TES', 'Testament'), ('SOC', 'Société'), ('ENT', 'Entreprise'), ('HYP', 'Hypothèque / garantie'), ('PRO', 'Procuration'), ('PAT', 'Patrimoine'), ('IND', 'Indivision'), ('PAR', 'Partage'), ('VEN', 'Vente'), ('BAI', 'Bail'), ('CRE', 'Créance / garantie'), ('DIV', 'Divorce / liquidation patrimoniale'), ('AUT', 'Autres actes')], default='AUT', max_length=4),
        ),
    ]
