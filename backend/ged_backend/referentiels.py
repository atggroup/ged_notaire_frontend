"""Référentiels fermés du cadrage documentaire (v1.0, 02/09/2026).

Chaque référentiel est une liste de tuples (code, libellé) utilisable
directement comme ``choices`` Django, plus un dict ``*_LABELS`` pour un accès
rapide par code. Les catégories internes (types documentaires) sont gardées
pour que les formulaires puissent afficher des groupes (<optgroup>).
"""

# 4. Référentiel des domaines (DOM)
DOMAINES = [
    ("IMM", "Immobilier"), ("FON", "Foncier"), ("SUC", "Succession"), ("DON", "Donation"),
    ("FAM", "Famille"), ("MAT", "Régime matrimonial"), ("TES", "Testament"), ("SOC", "Société"),
    ("ENT", "Entreprise"), ("HYP", "Hypothèque / garantie"), ("PRO", "Procuration"),
    ("PAT", "Patrimoine"), ("IND", "Indivision"), ("PAR", "Partage"), ("VEN", "Vente"),
    ("BAI", "Bail"), ("CRE", "Créance / garantie"), ("DIV", "Divorce / liquidation patrimoniale"),
    ("AUT", "Autres actes"),
]
DOMAINE_LABELS = dict(DOMAINES)

# 5. Référentiel des types documentaires (TYP), groupé par catégorie pour l'UI.
TYPES_DOCUMENTS_GROUPES = [
    ("Identification", [
        ("FIC", "Fiche client"), ("CNI", "Pièce d'identité"), ("PAS", "Passeport"),
        ("ETA", "État civil"), ("DOM", "Justificatif de domicile"),
        ("KYC", "Document de connaissance client"), ("MAN", "Mandat"),
        ("POU", "Pouvoir"), ("PRO", "Procuration"),
    ]),
    ("Documents juridiques", [
        ("DEM", "Demande"), ("CON", "Contrat"), ("TIT", "Titre"), ("CAD", "Document cadastral"),
        ("HYP", "Document hypothécaire"), ("STA", "Statuts"), ("RCC", "Document registre société"),
        ("CER", "Certificat"), ("ATT", "Attestation"), ("DEC", "Déclaration"),
        ("JUG", "Jugement / décision"), ("PV", "Procès-verbal"),
    ]),
    ("Actes", [
        ("PROJ", "Projet d'acte"), ("ACT", "Acte"), ("MIN", "Minute"), ("EXP", "Expédition"),
        ("COP", "Copie"), ("ANN", "Annexe"), ("REC", "Acte rectificatif"), ("DUP", "Duplicata"),
    ]),
    ("Correspondances", [
        ("CRR", "Courrier reçu"), ("CRE", "Courrier émis"), ("MEL", "Courriel"),
        ("CONV", "Convocation"), ("REL", "Relance"), ("AR", "Accusé de réception"),
        ("NOT", "Notification"),
    ]),
    ("Comptabilité", [
        ("DEV", "Devis"), ("PROV", "Provision"), ("FAC", "Facture"), ("RECPT", "Reçu"),
        ("PAY", "Paiement"), ("DEB", "Débours"), ("HON", "Honoraires"), ("REMB", "Remboursement"),
        ("SOL", "Solde"),
    ]),
    ("Gestion interne", [
        ("RAP", "Rapport"), ("NIN", "Note interne"), ("BOR", "Bordereau"), ("LIS", "Liste"),
        ("REG", "Registre"), ("FOM", "Formulaire"), ("AUD", "Audit"), ("INC", "Incident"),
    ]),
]
TYPES_DOCUMENTS = [code_label for _, items in TYPES_DOCUMENTS_GROUPES for code_label in items]
TYPE_DOCUMENT_LABELS = dict(TYPES_DOCUMENTS)

# 6. Statuts documentaires (STA) — référentiel complet du cadrage.
# L'application ne pilote qu'un sous-ensemble de ces statuts dans son
# workflow (voir documents.models.Document.Status) ; le mapping vers ce
# référentiel plus fin sert à composer le code notarial affiché (§2).
STATUTS = [
    ("REC", "Reçu"), ("BRO", "Brouillon"), ("ENC", "En cours"), ("REV", "En révision"),
    ("COR", "À corriger"), ("VAL", "Validé"), ("ASG", "À signer"), ("SIG", "Signé"),
    ("DEP", "Déposé"), ("ENR", "Enregistré"), ("TRA", "En traitement"), ("RET", "Retiré"),
    ("FOR", "Formalités terminées"), ("CLO", "Clôturé"), ("ARC", "Archivé"), ("ANN", "Annulé"),
    ("REM", "Remplacé"), ("REJ", "Rejeté"),
]
STATUT_LABELS = dict(STATUTS)

# 8.2 Origine documentaire
ORIGINES = [
    ("CLI", "Client"), ("CAB", "Cabinet"), ("ADM", "Administration"), ("BAN", "Banque"),
    ("JUD", "Autorité judiciaire"), ("ENT", "Entreprise"), ("EXP", "Expert"),
    ("CON", "Confrère"), ("AUT", "Autre"),
]
ORIGINE_LABELS = dict(ORIGINES)

# 8.3 Support
SUPPORTS = [
    ("PAP", "Papier"), ("NUM", "Numérique natif"), ("SCN", "Document numérisé"),
    ("HYB", "Papier + numérique"),
]
SUPPORT_LABELS = dict(SUPPORTS)

# 8.4 Nature / originalité
NATURES = [
    ("ORI", "Original"), ("COP", "Copie"), ("CAC", "Copie authentique/certifiée"),
    ("SCN", "Numérisation d'original"), ("DUP", "Duplicata"),
]
NATURE_LABELS = dict(NATURES)

# 9. Qualités des parties
QUALITES_PARTIES = [
    ("ACQ", "Acquéreur"), ("VEN", "Vendeur"), ("DON", "Donateur"), ("DNR", "Donataire"),
    ("HER", "Héritier"), ("DEF", "Défunt"), ("MAN", "Mandant"), ("MAND", "Mandataire"),
    ("BAI", "Bailleur"), ("LOC", "Locataire"), ("ASS", "Associé"), ("DIR", "Dirigeant"),
    ("CRE", "Créancier"), ("DEB", "Débiteur"), ("REP", "Représentant"),
]
QUALITE_PARTIE_LABELS = dict(QUALITES_PARTIES)
