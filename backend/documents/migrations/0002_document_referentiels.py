from django.db import migrations, models


TYPES_DOCUMENTS = [('FIC', 'Fiche client'), ('CNI', "Pièce d'identité"), ('PAS', 'Passeport'), ('ETA', 'État civil'), ('DOM', 'Justificatif de domicile'), ('KYC', 'Document de connaissance client'), ('MAN', 'Mandat'), ('POU', 'Pouvoir'), ('PRO', 'Procuration'), ('DEM', 'Demande'), ('CON', 'Contrat'), ('TIT', 'Titre'), ('CAD', 'Document cadastral'), ('HYP', 'Document hypothécaire'), ('STA', 'Statuts'), ('RCC', 'Document registre société'), ('CER', 'Certificat'), ('ATT', 'Attestation'), ('DEC', 'Déclaration'), ('JUG', 'Jugement / décision'), ('PV', 'Procès-verbal'), ('PROJ', "Projet d'acte"), ('ACT', 'Acte'), ('MIN', 'Minute'), ('EXP', 'Expédition'), ('COP', 'Copie'), ('ANN', 'Annexe'), ('REC', 'Acte rectificatif'), ('DUP', 'Duplicata'), ('CRR', 'Courrier reçu'), ('CRE', 'Courrier émis'), ('MEL', 'Courriel'), ('CONV', 'Convocation'), ('REL', 'Relance'), ('AR', 'Accusé de réception'), ('NOT', 'Notification'), ('DEV', 'Devis'), ('PROV', 'Provision'), ('FAC', 'Facture'), ('RECPT', 'Reçu'), ('PAY', 'Paiement'), ('DEB', 'Débours'), ('HON', 'Honoraires'), ('REMB', 'Remboursement'), ('SOL', 'Solde'), ('RAP', 'Rapport'), ('NIN', 'Note interne'), ('BOR', 'Bordereau'), ('LIS', 'Liste'), ('REG', 'Registre'), ('FOM', 'Formulaire'), ('AUD', 'Audit'), ('INC', 'Incident')]
ORIGINES = [('CLI', 'Client'), ('CAB', 'Cabinet'), ('ADM', 'Administration'), ('BAN', 'Banque'), ('JUD', 'Autorité judiciaire'), ('ENT', 'Entreprise'), ('EXP', 'Expert'), ('CON', 'Confrère'), ('AUT', 'Autre')]
SUPPORTS = [('PAP', 'Papier'), ('NUM', 'Numérique natif'), ('SCN', 'Document numérisé'), ('HYB', 'Papier + numérique')]
NATURES = [('ORI', 'Original'), ('COP', 'Copie'), ('CAC', 'Copie authentique/certifiée'), ('SCN', "Numérisation d'original"), ('DUP', 'Duplicata')]
QUALITES_PARTIES = [('ACQ', 'Acquéreur'), ('VEN', 'Vendeur'), ('DON', 'Donateur'), ('DNR', 'Donataire'), ('HER', 'Héritier'), ('DEF', 'Défunt'), ('MAN', 'Mandant'), ('MAND', 'Mandataire'), ('BAI', 'Bailleur'), ('LOC', 'Locataire'), ('ASS', 'Associé'), ('DIR', 'Dirigeant'), ('CRE', 'Créancier'), ('DEB', 'Débiteur'), ('REP', 'Représentant')]


class Migration(migrations.Migration):

    dependencies = [
        ('documents', '0001_initial'),
    ]

    operations = [
        migrations.AddField(model_name='document', name='sequence', field=models.PositiveIntegerField(default=0)),
        migrations.AddField(model_name='document', name='type_code', field=models.CharField(blank=True, choices=TYPES_DOCUMENTS, max_length=6)),
        migrations.AddField(model_name='document', name='origine', field=models.CharField(choices=ORIGINES, default='CAB', max_length=4)),
        migrations.AddField(model_name='document', name='support', field=models.CharField(choices=SUPPORTS, default='SCN', max_length=4)),
        migrations.AddField(model_name='document', name='nature', field=models.CharField(choices=NATURES, default='SCN', max_length=4)),
        migrations.AddField(model_name='document', name='qualite_partie', field=models.CharField(blank=True, choices=QUALITES_PARTIES, max_length=6)),
    ]
