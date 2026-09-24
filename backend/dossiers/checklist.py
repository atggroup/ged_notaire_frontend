"""Checklists de dossier.

    Création du dossier ─► modèles actifs du domaine ─► éléments « attendu »
    Dépôt d'une pièce (type_code) ─► élément correspondant « reçu, à vérifier »
    Coche humaine après contrôle ─► « complet »
    Tous les éléments requis complets ─► notification « dossier complet »

Rien n'est coché automatiquement : la présence d'un fichier ne prouve ni sa
lisibilité, ni sa validité, ni qu'il s'agit de la bonne partie.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import ChecklistTemplate, DossierChecklistItem


def generer_checklist(dossier) -> list[DossierChecklistItem]:
    """Crée les éléments issus des modèles du domaine. Idempotent : un
    élément par modèle et par dossier (contrainte d'unicité)."""
    crees = []
    for modele in ChecklistTemplate.objects.filter(domaine=dossier.domaine, active=True).order_by("order", "label"):
        try:
            with transaction.atomic():
                crees.append(DossierChecklistItem.objects.create(
                    dossier=dossier, label=modele.label, required=modele.required, source=DossierChecklistItem.Source.TEMPLATE,
                    template=modele, type_code=modele.type_code, reminder_days=modele.reminder_days))
        except IntegrityError:
            continue
    return crees


def rapprocher_piece(document) -> DossierChecklistItem | None:
    """Rattache une pièce déposée au premier élément attendu de même type."""
    if not document.dossier_id or not document.type_code:
        return None
    with transaction.atomic():
        item = (DossierChecklistItem.objects.select_for_update()
                .filter(dossier_id=document.dossier_id, type_code=document.type_code,
                        document__isnull=True, completed_at__isnull=True)
                .order_by("-required", "created_at").first())
        if item is None:
            return None
        item.document = document
        item.received_at = timezone.now()
        item.save(update_fields=["document", "received_at"])
    return item


def completude(dossier) -> dict:
    # Lecture en base, jamais depuis un cache préchargé (`prefetch_related`)
    # qui ne verrait pas la coche qu'on vient d'enregistrer.
    items = list(DossierChecklistItem.objects.filter(dossier_id=dossier.pk))
    requis = [i for i in items if i.required]
    manquants = [i for i in requis if not i.completed_at]
    return {
        "total": len(items), "requis": len(requis), "complets": len([i for i in items if i.completed_at]),
        "manquants": len(manquants), "recusAVerifier": len([i for i in items if i.document_id and not i.completed_at]),
        "complet": bool(requis) and not manquants,
    }


def item_payload(item: DossierChecklistItem) -> dict:
    return {
        "id": item.id, "label": item.label, "required": item.required, "completed": bool(item.completed_at),
        "completedAt": item.completed_at.isoformat() if item.completed_at else None,
        "state": item.state, "source": item.source, "typeCode": item.type_code,
        "document": item.document.reference if item.document_id else None,
        "receivedAt": item.received_at.isoformat() if item.received_at else None,
    }
