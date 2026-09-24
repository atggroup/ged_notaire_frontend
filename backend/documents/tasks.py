"""File OCR automatique.

    dépôt → en_attente → en_cours → extrait / indisponible
                            │
                            └─ erreur → nouvelle tentative (1 min, 10 min, 1 h)
                                        → au-delà : échec + notification

- la prise en charge est atomique (`UPDATE … WHERE ocr_status='en_attente'`) :
  deux exécutants ne traitent jamais la même pièce ;
- une pièce restée « en_cours » (exécutant tué en plein traitement) est
  remise en file après `OCR_STUCK_MINUTES` ;
- le binaire n'est jamais modifié et le texte extrait n'est jamais écrit
  dans les journaux techniques.
"""
from datetime import timedelta

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from audit.services import log_system_event
from core.jobs import job

from .models import Document
from .ocr import extract_text

RETRY_MINUTES = [1, 10, 60]


def erreur_definitive(exc: Exception) -> bool:
    """Un fichier structurellement illisible (PDF corrompu, chiffré par un
    tiers) ou indéchiffrable par la GED donnera la même erreur à chaque
    tentative : on le signale tout de suite. Une panne du moteur, un manque
    de mémoire ou un délai dépassé, eux, justifient une nouvelle tentative."""
    from cryptography.exceptions import InvalidTag
    if isinstance(exc, InvalidTag):
        return True
    return any(cls.__module__.split(".")[0] in {"pypdf", "PIL"} for cls in type(exc).__mro__)


def _notifier_echec(doc: Document) -> None:
    from notifications.models import Notification
    from notifications.services import active_admins, emit
    destinataires = [doc.uploaded_by, *[u for u in active_admins() if u.role == "admin"]]
    emit(destinataires, "ocr_failed", "OCR en échec",
         f"L'extraction du texte a échoué pour la pièce {doc.reference} "
         f"({'dossier ' + doc.dossier.reference if doc.dossier_id else 'sans dossier'}) après {doc.ocr_attempts} tentative(s). "
         "La pièce est conservée ; relancez l'OCR depuis sa fiche après vérification du fichier.",
         severity=Notification.Severity.WARNING, target_type="document", target_id=doc.reference,
         event_key=f"ocr-failed:{doc.pk}:{doc.ocr_attempts}")


def traiter_file_ocr(lot: int | None = None, now=None) -> dict:
    now = now or timezone.now()
    lot = max(1, lot or getattr(settings, "OCR_BATCH", 5))
    max_tentatives = max(1, getattr(settings, "OCR_MAX_ATTEMPTS", 3))

    bloques = Document.objects.filter(
        ocr_status=Document.OCRStatus.PROCESSING,
        ocr_started_at__lt=now - timedelta(minutes=getattr(settings, "OCR_STUCK_MINUTES", 30)))
    relances = bloques.update(ocr_status=Document.OCRStatus.PENDING, ocr_next_retry_at=now)

    candidats = list(
        Document.objects.filter(ocr_status=Document.OCRStatus.PENDING)
        .filter(Q(ocr_next_retry_at__isnull=True) | Q(ocr_next_retry_at__lte=now))
        .exclude(fichier="").exclude(statut=Document.Status.DESTROYED)
        .order_by("ocr_next_retry_at", "created_at").values_list("pk", flat=True)[:lot]
    )
    extraits = sans_texte = reessais = echecs = 0
    for pk in candidats:
        pris = Document.objects.filter(pk=pk, ocr_status=Document.OCRStatus.PENDING).update(
            ocr_status=Document.OCRStatus.PROCESSING, ocr_started_at=timezone.now())
        if not pris:
            continue  # pris par un autre exécutant entre-temps
        doc = Document.objects.select_related("dossier", "uploaded_by").get(pk=pk)
        doc.ocr_attempts += 1
        try:
            texte, etat = extract_text(doc.decrypted_bytes(), doc.content_type)
        except Exception as exc:  # noqa: BLE001 — une pièce illisible ne bloque pas la file
            doc.ocr_error = str(exc)[:1000]
            if not erreur_definitive(exc) and doc.ocr_attempts < max_tentatives:
                doc.ocr_status = Document.OCRStatus.PENDING
                doc.ocr_next_retry_at = timezone.now() + timedelta(minutes=RETRY_MINUTES[min(doc.ocr_attempts - 1, len(RETRY_MINUTES) - 1)])
                reessais += 1
            else:
                doc.ocr_status = Document.OCRStatus.FAILED
                doc.ocr_next_retry_at = None
                doc.ocr_processed_at = timezone.now()
                echecs += 1
            doc.save(update_fields=["ocr_status", "ocr_error", "ocr_attempts", "ocr_next_retry_at", "ocr_processed_at", "updated_at"])
            if doc.ocr_status == Document.OCRStatus.FAILED:
                log_system_event("document_ocr_failed", "document", doc.reference, result="failure",
                                 metadata={"tentatives": doc.ocr_attempts, "erreur": doc.ocr_error[:200]})
                _notifier_echec(doc)
            continue
        doc.extracted_text = texte
        doc.ocr_status = Document.OCRStatus.EXTRACTED if etat == "extrait" else Document.OCRStatus.UNAVAILABLE
        doc.ocr_error = ""
        doc.ocr_next_retry_at = None
        doc.ocr_processed_at = timezone.now()
        doc.save(update_fields=["extracted_text", "ocr_status", "ocr_error", "ocr_attempts", "ocr_next_retry_at", "ocr_processed_at", "updated_at"])
        log_system_event("document_ocr_processed", "document", doc.reference,
                         metadata={"status": doc.ocr_status, "characters": len(texte), "tentatives": doc.ocr_attempts})
        if doc.ocr_status == Document.OCRStatus.EXTRACTED:
            extraits += 1
        else:
            sans_texte += 1
    restant = Document.objects.filter(ocr_status=Document.OCRStatus.PENDING).exclude(fichier="").exclude(statut=Document.Status.DESTROYED).count()
    traites = extraits + sans_texte + echecs
    return {"items": traites + reessais + relances,
            "message": f"extraits : {extraits} | sans texte exploitable : {sans_texte} | à retenter : {reessais} | "
                       f"échecs définitifs : {echecs} | remis en file : {relances} | encore en attente : {restant}"}


@job("ocr", "Extraction de texte (OCR) des pièces déposées", every=60, lock_ttl=1800)
def ocr():
    return traiter_file_ocr()
