from .models import Permission


def has_document_access(user, document) -> bool:
    if user.role == "admin":
        return True
    grant = Permission.objects.filter(user=user).filter(document=document).first() or Permission.objects.filter(user=user, dossier=document.dossier).first()
    # Only the notaire selects a non-standard level. Both protected levels
    # require an explicit, auditable grant; a clerc's job title alone does
    # not open a client's file.
    if document.niveau_de_confidentialite == "Très confidentiel":
        # Very confidential material is never opened merely because a user is
        # assigned to the dossier; an explicit grant is required.
        return bool(grant)
    if document.niveau_de_confidentialite != "Standard":
        return bool(grant) or bool(document.dossier_id and document.dossier.assignments.filter(user=user).exists())
    return True


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
