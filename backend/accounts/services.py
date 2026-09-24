"""Réévaluation de l'exigence de second facteur après un changement de droits.

L'application exige un second facteur selon le **risque** : toujours pour le
notaire, et pour tout compte détenant un accès à du « Confidentiel » ou du
« Très confidentiel » (voir `LoginView`). Cette décision est prise **à la
connexion**.

Sans le présent module, accorder une habilitation confidentielle à un compte
déjà connecté par simple mot de passe lui ouvrait immédiatement la pièce, avec
un jeton valable 8 heures que la politique n'aurait jamais dû couvrir. On
révoque donc la session concernée : la reconnexion repassera par `LoginView`,
qui exigera cette fois le code à usage unique.
"""
from ged_backend.confidentialite import rang

# Au-delà de « Restreint », l'accès relève du second facteur.
_SEUIL_MFA = rang("Confidentiel")


def _niveaux_concernes(*objets) -> bool:
    for objet in objets:
        if objet is None:
            continue
        if rang(getattr(objet, "niveau_de_confidentialite", "Standard")) >= _SEUIL_MFA:
            return True
    return False


def reevaluer_exigence_mfa(user, *objets, request=None) -> bool:
    """Révoque la session de `user` si un accès confidentiel vient de lui être
    ouvert alors que sa session courante n'a pas franchi de second facteur.

    Renvoie True si la session a été révoquée."""
    if user is None or not _niveaux_concernes(*objets):
        return False
    if user.session_mfa_verified:
        # La session courante est déjà couverte par un second facteur.
        return False
    user.session_version += 1
    user.session_mfa_verified = False
    user.save(update_fields=["session_version", "session_mfa_verified"])
    if request is not None:
        from audit.services import log_event
        log_event(
            request, "session_revoked_mfa_required", "user", str(user.pk),
            metadata={"motif": "acces confidentiel accorde a une session sans second facteur"},
        )
    return True
