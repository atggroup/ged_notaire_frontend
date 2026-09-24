"""Ordre des niveaux de confidentialité, partagé par les dossiers et les documents.

Règle du cadrage : **la politique du dossier est un plancher**. Le notaire peut
poser sur une pièce une exception plus restrictive que son dossier, jamais plus
ouverte — sans quoi une pièce « Standard » déposée dans un dossier « Très
confidentiel » redeviendrait lisible par toute l'étude.

Les deux modèles (`Dossier.Confidentiality` et `Document.Confidentiality`)
partagent exactement le même vocabulaire ; ce module en fixe l'ordre.
"""

NIVEAUX = ["Standard", "Restreint", "Confidentiel", "Très confidentiel"]
_RANG = {niveau: index for index, niveau in enumerate(NIVEAUX)}


def rang(niveau: str) -> int:
    """Position d'un niveau dans l'échelle ; un libellé inconnu vaut le plus ouvert."""
    return _RANG.get(niveau, 0)


def plus_restrictif(niveau: str, plancher: str) -> str:
    """Renvoie celui des deux niveaux qui protège le plus."""
    return niveau if rang(niveau) >= rang(plancher) else plancher


def est_plus_faible(niveau: str, plancher: str) -> bool:
    """Vrai si `niveau` ouvre davantage que `plancher` ne l'autorise."""
    return rang(niveau) < rang(plancher)


def niveau_effectif(document) -> str:
    """Niveau réellement opposable à une pièce : le sien, relevé par celui de
    son dossier. Défense en profondeur — les pièces enregistrées avant la
    correction du plancher restent protégées sans reprise de données."""
    niveau = document.niveau_de_confidentialite
    dossier = getattr(document, "dossier", None)
    if dossier is None:
        return niveau
    return plus_restrictif(niveau, dossier.niveau_de_confidentialite)
