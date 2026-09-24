"""Règle d'habilitation documentaire — une seule définition, deux formes.

`has_document_access` tranche pour UNE pièce (contrôle unitaire d'une vue de
détail) ; `documents_visibles_par` applique exactement la même règle sous forme
de filtre SQL, pour ne plus charger une table entière en mémoire puis la
parcourir pièce par pièce en déclenchant une requête à chaque tour.

Les deux fonctions doivent rester d'accord : toute évolution de l'une se
répercute sur l'autre (cf. `tests/test_access_parite.py`).
"""
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Greatest

from ged_backend.confidentialite import NIVEAUX, niveau_effectif
from .models import Permission

_OUVERT = 0                      # « Standard »
_TRES_CONFIDENTIEL = len(NIVEAUX) - 1


def _rang(champ: str):
    """Traduit un libellé de niveau en rang numérique, côté base."""
    return Case(
        *[When(**{champ: niveau}, then=Value(index)) for index, niveau in enumerate(NIVEAUX)],
        default=Value(_OUVERT),
        output_field=IntegerField(),
    )


def has_document_access(user, document) -> bool:
    if user.role == "admin":
        return True
    # Le niveau opposable est celui de la pièce RELEVÉ par celui de son dossier :
    # une pièce « Standard » rangée dans un dossier « Très confidentiel » reste
    # protégée, même si elle a été enregistrée avant que le plancher ne soit posé
    # à l'écriture (voir ged_backend/confidentialite.py).
    niveau = niveau_effectif(document)
    if niveau == "Standard":
        return True
    grant = (
        Permission.objects.filter(user=user, document=document).exists()
        or (document.dossier_id is not None and Permission.objects.filter(user=user, dossier_id=document.dossier_id).exists())
    )
    # Only the notaire selects a non-standard level. Both protected levels
    # require an explicit, auditable grant; a clerc's job title alone does
    # not open a client's file.
    if niveau == "Très confidentiel":
        # Very confidential material is never opened merely because a user is
        # assigned to the dossier; an explicit grant is required.
        return grant
    return grant or bool(document.dossier_id and document.dossier.assignments.filter(user=user).exists())


def documents_visibles_par(user, queryset):
    """Restreint un queryset de documents à ce que `user` a le droit de voir.

    Même règle que `has_document_access`, exprimée en SQL : le filtrage se fait
    dans la base, ce qui rend la pagination possible et supprime le N+1."""
    if user.role == "admin":
        return queryset
    habilite = Q(access_permissions__user=user) | Q(dossier__access_permissions__user=user)
    affecte = Q(dossier__assignments__user=user)
    queryset = queryset.annotate(
        _niveau_rang=Greatest(
            _rang("niveau_de_confidentialite"),
            _rang("dossier__niveau_de_confidentialite"),
        )
    )
    return queryset.filter(
        Q(_niveau_rang=_OUVERT)
        | (Q(_niveau_rang=_TRES_CONFIDENTIEL) & habilite)
        | (Q(_niveau_rang__gt=_OUVERT) & ~Q(_niveau_rang=_TRES_CONFIDENTIEL) & (habilite | affecte))
    ).distinct()


def has_dossier_access(user, dossier) -> bool:
    if user.role == "admin":
        return True
    if dossier.niveau_de_confidentialite == "Standard":
        return True
    if Permission.objects.filter(user=user, dossier=dossier).exists():
        return True
    # An assignment is an auditable dossier-level entitlement, unlike a
    # one-off document exception.
    return dossier.assignments.filter(user=user).exists()


def dossiers_visibles_par(user, queryset):
    """Pendant SQL de `has_dossier_access`."""
    if user.role == "admin":
        return queryset
    return queryset.filter(
        Q(niveau_de_confidentialite="Standard")
        | Q(access_permissions__user=user)
        | Q(assignments__user=user)
    ).distinct()
