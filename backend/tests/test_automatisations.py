"""Automatisations : preuve de fiabilité.

Chaque test rejoue une propriété exigée d'une automatisation professionnelle :
elle agit une seule fois (idempotence), elle se retente, elle ne tombe jamais
en silence (trace + alerte), elle respecte les droits, et elle ne prend
aucune décision juridique à la place d'un humain.
"""
import base64
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path

import pytest
from django.core import mail
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import connection
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OTPCode, User
from audit.models import AuditLog, SecurityAlert
from core.jobs import JobSpec, REGISTRY, is_due, registry, run_job
from core.models import JobRun
from documents.crypto import encrypt
from documents.models import Document
from dossiers.models import ChecklistTemplate, Dossier, DossierAssignment, DossierChecklistItem, PhysicalArchiveRecord
from notifications.models import Notification, Task
from settings_app.models import BackupRun

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
PDF = b"%PDF-1.4\n% piece de test\ntrailer<</Root 1 0 R>>\n%%EOF\n"
CHIFFREMENT = dict(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")


@pytest.fixture(autouse=True)
def _cache_propre():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


def _etude():
    notaire = User.objects.create_user(email="notaire@etude.ci", password="Mdp!123456", role="admin", first_name="Awa", last_name="Notaire")
    clerc = User.objects.create_user(email="clerc@etude.ci", password="Mdp!123456", role="clerc", first_name="Koffi", last_name="Clerc")
    collab = User.objects.create_user(email="collab@etude.ci", password="Mdp!123456", role="collaborateur")
    dossier = Dossier.objects.create(reference="VEN-2026-00001", domaine="VEN", nom="Vente Cocody", client="M. Yao",
                                     niveau_de_confidentialite="Standard", created_by=notaire)
    DossierAssignment.objects.create(dossier=dossier, user=clerc, role=DossierAssignment.Role.CLERK, assigned_by=notaire)
    return notaire, clerc, collab, dossier


def _piece(reference, dossier, auteur, **extra):
    chiffre, key_id = encrypt(PDF)
    doc = Document.objects.create(
        reference=reference, dossier=dossier, type="Acte", nom=f"{reference}.pdf", original_filename=f"Testament_{reference}.pdf",
        content_type="application/pdf", size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(), uploaded_by=auteur,
        master_reference=reference, encryption_key_id=key_id, **extra)
    doc.fichier.save(f"{reference}.pdf", ContentFile(chiffre), save=True)
    return doc


def _vieillir(objet, **delta):
    """Recule `created_at` (auto_now_add) sans passer par save()."""
    type(objet).objects.filter(pk=objet.pk).update(created_at=timezone.now() - timedelta(**delta))
    objet.refresh_from_db()


# ===========================================================================
# 1. Socle : exécution tracée, verrou, échec jamais muet, échéancier
# ===========================================================================

@pytest.fixture
def travail_de_test():
    etat = {"echouer": False, "appels": 0}

    def fonction():
        etat["appels"] += 1
        if etat["echouer"]:
            raise RuntimeError("panne simulée")
        return {"items": 2, "message": "deux éléments"}

    registry()
    REGISTRY["test_job"] = JobSpec(name="test_job", func=fonction, label="Travail de test", daily_at="02:00", retries=1, retry_delay=60)
    yield etat
    REGISTRY.pop("test_job", None)


@pytest.mark.django_db
def test_une_execution_reussie_est_tracee_et_journalisee(travail_de_test):
    run = run_job("test_job")
    assert run.status == JobRun.Status.SUCCESS and run.items == 2
    assert AuditLog.objects.filter(action="job_test_job", result="success").exists()


@pytest.mark.django_db
def test_un_echec_est_trace_journalise_et_notifie_aux_notaires(travail_de_test):
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    travail_de_test["echouer"] = True
    run = run_job("test_job")
    assert run.status == JobRun.Status.FAILURE
    assert "panne simulée" in run.error
    assert AuditLog.objects.filter(action="job_test_job", result="failure").exists()
    alerte = Notification.objects.get(recipient=notaire, type="job_failure")
    assert "Nouvelle tentative" in alerte.message
    # Une seconde panne le même jour ne réinonde pas la cloche…
    run_job("test_job")
    finale = Notification.objects.filter(recipient=notaire, type="job_failure")
    # … mais la tentative finale (épuisée) est signalée comme critique.
    assert finale.filter(severity="critique").count() == 1


@pytest.mark.django_db
def test_un_travail_deja_en_cours_ailleurs_n_est_pas_lance_deux_fois(travail_de_test):
    from core.coordination import acquire_lock
    assert acquire_lock("job:test_job", 600)
    assert run_job("test_job") is None
    assert travail_de_test["appels"] == 0


@pytest.mark.django_db
def test_l_echeancier_retente_puis_s_arrete(travail_de_test):
    spec = REGISTRY["test_job"]
    maintenant = timezone.now()
    assert is_due(spec, maintenant)
    travail_de_test["echouer"] = True
    run_job("test_job")
    assert not is_due(spec, timezone.now()), "le délai avant nouvelle tentative doit être respecté"
    assert is_due(spec, timezone.now() + timedelta(seconds=61)), "une nouvelle tentative est due après le délai"
    JobRun.objects.update(finished_at=timezone.now() - timedelta(seconds=120))
    run_job("test_job")
    JobRun.objects.update(finished_at=timezone.now() - timedelta(seconds=120))
    assert not is_due(spec, timezone.now()), "tentatives épuisées : plus de relance automatique"


@pytest.mark.django_db
def test_la_demande_depuis_l_ecran_est_executee_par_l_executant(travail_de_test):
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c@x.ci", password="Mdp!123456", role="clerc")
    assert _client(clerc).post("/api/automation/jobs/test_job/runs").status_code == 403
    reponse = _client(notaire).post("/api/automation/jobs/test_job/runs")
    assert reponse.status_code == 202 and reponse.data["status"] == JobRun.Status.REQUESTED
    assert travail_de_test["appels"] == 0, "rien ne s'exécute dans la requête HTTP"
    call_command("run_worker", "--une-fois", "--sauf-groupe", "sauvegarde", stdout=io.StringIO())
    run = JobRun.objects.get(pk=reponse.data["id"])
    assert run.status == JobRun.Status.SUCCESS and run.triggered_by == notaire
    assert AuditLog.objects.filter(action="job_requested", user=notaire).exists()
    liste = _client(notaire).get("/api/automation/jobs").data
    assert any(j["name"] == "test_job" for j in liste["jobs"])
    assert liste["workers"] and liste["workers"][0]["alive"]


# ===========================================================================
# 2. Références sans collision
# ===========================================================================

@pytest.mark.django_db
def test_les_references_de_dossier_reprennent_l_existant_sans_doublon():
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    annee = timezone.localdate().year
    Dossier.objects.create(reference=f"SUC-{annee}-00007", domaine="SUC", nom="Ancien", client="X", created_by=notaire)
    refs = [_client(notaire).post("/api/dossiers", {"nom": f"D{i}", "client": "Famille A", "domaine": "SUC"}, format="json").data["reference"] for i in range(3)]
    assert refs == [f"SUC-{annee}-00008", f"SUC-{annee}-00009", f"SUC-{annee}-00010"]


@pytest.mark.django_db
def test_un_dossier_ne_peut_pas_naitre_archive():
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    reponse = _client(notaire).post("/api/dossiers", {"nom": "D", "client": "C", "domaine": "VEN", "statut": "archivé"}, format="json")
    assert reponse.status_code == 400


# ===========================================================================
# 3. Échéances : J-7, J-3, J-1, jour J, retard, escalade
# ===========================================================================

def _tache(assignee, donneur, echeance, cree_il_y_a=None, **extra):
    tache = Task.objects.create(title="Obtenir l'état foncier", assigned_to=assignee, assigned_by=donneur, due_at=echeance, **extra)
    if cree_il_y_a:
        _vieillir(tache, **cree_il_y_a)
    return tache


@pytest.mark.django_db
def test_les_paliers_partent_une_fois_chacun():
    from notifications.tasks import envoyer_rappels_taches
    notaire, clerc, _c, dossier = _etude()
    # Instant fixé à midi : à 23 h, J-1 et « jour J » coïncident et seul le
    # plus récent part (comportement voulu) — le test ne doit pas dépendre de l'heure.
    maintenant = timezone.localtime().replace(hour=12, minute=0, second=0, microsecond=0)
    tache = _tache(clerc, notaire, maintenant + timedelta(days=6, hours=23), cree_il_y_a={"days": 10}, dossier=dossier)

    envoyer_rappels_taches(maintenant)
    envoyer_rappels_taches(maintenant)
    assert list(Notification.objects.filter(recipient=clerc, type="task_deadline").values_list("title", flat=True)) == \
        ["Échéance proche : échéance dans 7 jours"]

    envoyer_rappels_taches(maintenant + timedelta(days=4))  # J-3 atteint
    envoyer_rappels_taches(maintenant + timedelta(days=6, hours=1))  # J-1 atteint
    titres = list(Notification.objects.filter(recipient=clerc, type="task_deadline").order_by("pk").values_list("title", flat=True))
    assert titres == ["Échéance proche : échéance dans 7 jours", "Échéance proche : échéance dans 3 jours", "Échéance proche : échéance dans 1 jour"]
    assert AuditLog.objects.filter(action="task_reminder_sent", target_id=str(tache.pk)).count() == 3
    assert "VEN-2026-00001" in Notification.objects.filter(recipient=clerc).first().message
    # La doublure e-mail est mise en file, pas envoyée dans le passage.
    assert Notification.objects.filter(recipient=clerc, email_status="en_attente").count() == 3


@pytest.mark.django_db
def test_apres_une_panne_seul_le_palier_le_plus_recent_part():
    from notifications.tasks import envoyer_rappels_taches
    notaire, clerc, _c, _d = _etude()
    maintenant = timezone.now()
    _tache(clerc, notaire, maintenant + timedelta(hours=20), cree_il_y_a={"days": 10})
    envoyer_rappels_taches(maintenant)
    rappels = Notification.objects.filter(recipient=clerc, type="task_deadline")
    assert rappels.count() == 1, "personne ne reçoit trois rappels d'un coup"
    assert "1 jour" in rappels.first().title or "aujourd'hui" in rappels.first().title


@pytest.mark.django_db
def test_une_tache_creee_tardivement_ne_recoit_pas_de_j_moins_7():
    from notifications.tasks import envoyer_rappels_taches
    notaire, clerc, _c, _d = _etude()
    _tache(clerc, notaire, timezone.now() + timedelta(days=2))
    envoyer_rappels_taches()
    assert not Notification.objects.filter(type="task_deadline").exists()


@pytest.mark.django_db
def test_le_retard_persistant_est_escalade_au_notaire_superviseur():
    from notifications.tasks import envoyer_rappels_taches
    notaire, clerc, _c, dossier = _etude()
    superviseur = User.objects.create_user(email="sup@etude.ci", password="Mdp!123456", role="admin")
    DossierAssignment.objects.create(dossier=dossier, user=superviseur, role=DossierAssignment.Role.SUPERVISOR, assigned_by=notaire)
    tache = _tache(clerc, notaire, timezone.now() - timedelta(days=3), cree_il_y_a={"days": 10}, dossier=dossier)
    envoyer_rappels_taches()
    envoyer_rappels_taches()
    assert Notification.objects.filter(type="task_escalation").count() == 1
    assert Notification.objects.get(type="task_escalation").recipient == superviseur
    tache.refresh_from_db()
    assert tache.escalated_at and tache.overdue_notified_at


@pytest.mark.django_db
def test_l_echeance_d_affectation_est_rappelee():
    from notifications.tasks import envoyer_rappels_taches
    notaire, clerc, _c, dossier = _etude()
    DossierAssignment.objects.filter(dossier=dossier, user=clerc).update(due_at=timezone.now() - timedelta(hours=1))
    envoyer_rappels_taches()
    envoyer_rappels_taches()
    assert Notification.objects.filter(type="assignment_due", recipient=clerc).count() == 1
    assert Notification.objects.filter(type="assignment_due", recipient=notaire).count() == 1


# ===========================================================================
# 4. E-mails : file, relances, échec définitif signalé
# ===========================================================================

@pytest.mark.django_db
def test_l_email_est_envoye_depuis_la_file():
    from notifications.services import notify, send_pending_emails
    notaire, clerc, _c, _d = _etude()
    notify(clerc, "task", "Nouvelle tâche", "Relancer le cadastre", email=True)
    assert len(mail.outbox) == 0
    send_pending_emails()
    assert len(mail.outbox) == 1 and mail.outbox[0].to == ["clerc@etude.ci"]
    send_pending_emails()
    assert len(mail.outbox) == 1, "un e-mail envoyé ne repart pas"


@pytest.mark.django_db
def test_une_panne_smtp_est_retentee_puis_signalee(monkeypatch):
    import django.core.mail as dj_mail
    from notifications import services
    notaire, clerc, _c, _d = _etude()
    services.notify(clerc, "task", "Nouvelle tâche", "Relancer le cadastre", email=True)

    def panne(*a, **k):
        raise OSError("SMTP injoignable")
    monkeypatch.setattr(dj_mail, "send_mail", panne)
    notif = Notification.objects.get(recipient=clerc, type="task")
    for _ in range(len(services.EMAIL_BACKOFF_MINUTES)):
        Notification.objects.filter(pk=notif.pk).update(next_email_at=timezone.now())
        services.send_pending_emails()
    notif.refresh_from_db()
    assert notif.email_status == Notification.EmailStatus.FAILED
    assert notif.email_attempts == len(services.EMAIL_BACKOFF_MINUTES)
    assert Notification.objects.filter(recipient=notaire, type="email_failure").count() == 1


# ===========================================================================
# 5. OCR : prise en charge, relances, échec signalé, reprise
# ===========================================================================

@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_une_erreur_transitoire_d_ocr_est_retentee_puis_signalee(monkeypatch):
    from documents import tasks
    notaire, clerc, _c, dossier = _etude()
    doc = _piece("DOC_OCR1", dossier, clerc)

    def panne(*a, **k):
        raise MemoryError("mémoire insuffisante")
    monkeypatch.setattr(tasks, "extract_text", panne)
    tasks.traiter_file_ocr()
    doc.refresh_from_db()
    assert doc.ocr_status == Document.OCRStatus.PENDING and doc.ocr_attempts == 1 and doc.ocr_next_retry_at
    tasks.traiter_file_ocr()
    doc.refresh_from_db()
    assert doc.ocr_attempts == 1, "la nouvelle tentative attend son délai"
    for _ in range(2):
        Document.objects.filter(pk=doc.pk).update(ocr_next_retry_at=timezone.now())
        tasks.traiter_file_ocr()
    doc.refresh_from_db()
    assert doc.ocr_status == Document.OCRStatus.FAILED and doc.ocr_attempts == 3
    assert Notification.objects.filter(type="ocr_failed", recipient=clerc).count() == 1
    assert AuditLog.objects.filter(action="document_ocr_failed", target_id="DOC_OCR1", result="failure").exists()


@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_un_ocr_bloque_est_remis_en_file_et_un_succes_est_journalise(monkeypatch):
    from documents import tasks
    notaire, clerc, _c, dossier = _etude()
    doc = _piece("DOC_OCR2", dossier, clerc)
    Document.objects.filter(pk=doc.pk).update(ocr_status=Document.OCRStatus.PROCESSING, ocr_started_at=timezone.now() - timedelta(hours=2))
    monkeypatch.setattr(tasks, "extract_text", lambda data, ct: ("VENTE PARCELLE 12", "extrait"))
    tasks.traiter_file_ocr()
    doc.refresh_from_db()
    assert doc.ocr_status == Document.OCRStatus.EXTRACTED and doc.extracted_text == "VENTE PARCELLE 12"
    assert AuditLog.objects.filter(action="document_ocr_processed", target_id="DOC_OCR2").exists()
    assert "VENTE PARCELLE" not in json.dumps(list(AuditLog.objects.values_list("metadata", flat=True))), \
        "le texte extrait n'entre jamais dans le journal"
    reponse = _client(notaire).get("/api/search", {"q": "parcelle"})
    assert [d["reference"] for d in reponse.data] == ["DOC_OCR2"]


# ===========================================================================
# 6. Checklists et pièces manquantes
# ===========================================================================

@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_la_checklist_est_generee_puis_rapprochee_sans_etre_cochee():
    from django.core.files.uploadedfile import SimpleUploadedFile
    call_command("charger_modeles_checklist", stdout=io.StringIO())
    call_command("charger_modeles_checklist", stdout=io.StringIO())  # idempotent
    assert ChecklistTemplate.objects.filter(domaine="VEN").count() == 8
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    reponse = _client(notaire).post("/api/dossiers", {"nom": "Vente", "client": "M. Yao", "domaine": "VEN", "niveau": "Standard"}, format="json")
    assert reponse.status_code == 201 and reponse.data["checklistItems"] == 8
    ref = reponse.data["reference"]

    depot = _client(notaire).post("/api/documents/upload", {"fichier": SimpleUploadedFile("titre.pdf", PDF, content_type="application/pdf"),
                                                              "type_code": "TIT", "dossier": ref}, format="multipart")
    assert depot.status_code == 201 and depot.data["checklistItem"]
    item = DossierChecklistItem.objects.get(pk=depot.data["checklistItem"])
    assert item.type_code == "TIT" and item.document.reference == depot.data["reference"]
    assert item.completed_at is None, "la présence d'un fichier ne vaut pas complétude"
    detail = _client(notaire).get(f"/api/dossiers/{ref}").data
    assert any(c["state"] == "reçu_à_vérifier" for c in detail["checklist"])

    # Même fichier déposé deux fois : signalé comme doublon.
    second = _client(notaire).post("/api/documents/upload", {"fichier": SimpleUploadedFile("titre2.pdf", PDF, content_type="application/pdf"),
                                                               "type_code": "TIT", "dossier": ref}, format="multipart")
    assert any("identique" in w for w in second.data["warnings"])


@pytest.mark.django_db
def test_le_dossier_complet_est_annonce_une_fois():
    notaire, clerc, _c, dossier = _etude()
    items = [DossierChecklistItem.objects.create(dossier=dossier, label=l, required=True) for l in ("CNI", "Titre")]
    c = _client(clerc)
    c.patch(f"/api/dossiers/{dossier.reference}/checklist", {"id": items[0].pk, "completed": True}, format="json")
    assert not Notification.objects.filter(type="dossier_complete").exists()
    reponse = c.patch(f"/api/dossiers/{dossier.reference}/checklist", {"id": items[1].pk, "completed": True}, format="json")
    assert reponse.data["completude"]["complet"] is True
    assert Notification.objects.filter(type="dossier_complete", recipient=notaire).count() == 1
    c.patch(f"/api/dossiers/{dossier.reference}/checklist", {"id": items[1].pk, "completed": True}, format="json")
    assert Notification.objects.filter(type="dossier_complete").count() == 1


@pytest.mark.django_db
def test_une_piece_manquante_cree_une_seule_tache_de_relance():
    from dossiers.tasks import relancer_pieces_manquantes
    notaire, clerc, _c, dossier = _etude()
    item = DossierChecklistItem.objects.create(dossier=dossier, label="État foncier", required=True)
    DossierChecklistItem.objects.create(dossier=dossier, label="Facultatif", required=False)
    relancer_pieces_manquantes()
    assert not Task.objects.exists(), "le délai de relance n'est pas écoulé"
    _vieillir(item, days=10)
    relancer_pieces_manquantes()
    relancer_pieces_manquantes()
    tache = Task.objects.get()
    assert tache.source == Task.Source.AUTO and tache.assigned_to == clerc and tache.dossier == dossier
    assert Notification.objects.filter(type="missing_documents", recipient=clerc).count() == 1
    assert AuditLog.objects.filter(action="missing_documents_detected").count() == 1
    # Un dossier gelé n'est pas relancé.
    Task.objects.all().delete()
    item2 = DossierChecklistItem.objects.create(dossier=dossier, label="Plan", required=True)
    _vieillir(item2, days=10)
    Dossier.objects.filter(pk=dossier.pk).update(legal_hold=True)
    relancer_pieces_manquantes()
    assert not Task.objects.exists()


@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_une_piece_expiree_est_signalee_et_une_tache_creee():
    from dossiers.tasks import signaler_expirations
    notaire, clerc, _c, dossier = _etude()
    _piece("DOC_CNI", dossier, clerc, valid_until=timezone.localdate() - timedelta(days=1))
    _piece("DOC_CNI2", dossier, clerc, valid_until=timezone.localdate() + timedelta(days=10))
    signaler_expirations()
    signaler_expirations()
    assert Notification.objects.filter(type="document_expiry", recipient=clerc).count() == 2
    assert Task.objects.filter(source=Task.Source.AUTO, title__startswith="Pièce expirée").count() == 1


@pytest.mark.django_db
def test_un_original_sorti_trop_longtemps_est_rappele():
    from dossiers.tasks import rappeler_retours_originaux
    notaire, clerc, _c, dossier = _etude()
    PhysicalArchiveRecord.objects.create(dossier=dossier, room="A", cabinet="1", shelf="2", box="3", folder="4",
                                         checked_out_by=clerc, checked_out_at=timezone.now() - timedelta(days=20), checkout_reason="Signature")
    rappeler_retours_originaux()
    rappeler_retours_originaux()
    assert Notification.objects.filter(type="physical_return", recipient=clerc).count() == 1


# ===========================================================================
# 7. Workflow : les étapes juridiques restent humaines et gardées
# ===========================================================================

@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_la_validation_exige_une_piece_en_validation():
    notaire, clerc, _c, dossier = _etude()
    corbeille = _piece("DOC_TR", dossier, clerc, quality_passed=True, statut=Document.Status.TRASHED, trashed_at=timezone.now())
    assert _client(notaire).post(f"/api/documents/{corbeille.reference}/validate").status_code == 409
    corbeille.refresh_from_db()
    assert corbeille.statut == Document.Status.TRASHED


@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_le_controle_qualite_ne_retire_pas_une_validation_et_notifie():
    notaire, clerc, _c, dossier = _etude()
    valide = _piece("DOC_VAL", dossier, clerc, quality_passed=True, statut=Document.Status.VALIDATED)
    checks = {k: True for k in ("complete", "ordered", "legible", "noMissingPage", "noDuplicate", "orientationCorrect", "dossierCorrect")}
    assert _client(clerc).post(f"/api/documents/{valide.reference}/quality-check", {"checks": checks}, format="json").status_code == 409

    a_controler = _piece("DOC_QC", dossier, clerc, statut=Document.Status.TO_INDEX)
    rate = dict(checks, legible=False)
    _client(notaire).post(f"/api/documents/{a_controler.reference}/quality-check", {"checks": rate, "notes": "Page 3 floue"}, format="json")
    notif = Notification.objects.get(recipient=clerc, type="document_to_fix")
    assert "Page 3 floue" in notif.message and notif.target_id == "DOC_QC"
    _client(clerc).post(f"/api/documents/{a_controler.reference}/quality-check", {"checks": checks}, format="json")
    assert Notification.objects.filter(recipient=notaire, type="document_to_validate").count() == 1


@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_une_version_ne_se_depose_que_sur_la_version_courante():
    from django.core.files.uploadedfile import SimpleUploadedFile
    notaire, clerc, _c, dossier = _etude()
    v1 = _piece("DOC_V1", dossier, clerc, statut=Document.Status.VALIDATED, quality_passed=True)
    c = _client(clerc)
    v2 = c.post(f"/api/documents/{v1.reference}/versions", {"fichier": SimpleUploadedFile("v2.pdf", PDF, content_type="application/pdf")}, format="multipart")
    assert v2.status_code == 201 and v2.data["version"] == 2
    branche = c.post(f"/api/documents/{v1.reference}/versions", {"fichier": SimpleUploadedFile("v3.pdf", PDF, content_type="application/pdf")}, format="multipart")
    assert branche.status_code == 409
    assert Document.objects.filter(master_reference="DOC_V1", is_current=True).count() == 1
    notif = Notification.objects.get(recipient=notaire, type="document_new_version")
    assert "repasser le contrôle" in notif.message


@pytest.mark.django_db
def test_le_clerc_ne_cloture_ni_n_archive_un_dossier():
    notaire, clerc, _c, dossier = _etude()
    c = _client(clerc)
    assert c.patch(f"/api/dossiers/{dossier.reference}", {"statut": "archivé"}, format="json").status_code == 403
    assert c.patch(f"/api/dossiers/{dossier.reference}", {"statut": "pret_pour_acte"}, format="json").status_code == 403
    reponse = c.patch(f"/api/dossiers/{dossier.reference}", {"statut": "en_instruction"}, format="json")
    assert reponse.status_code == 200
    assert AuditLog.objects.filter(action="dossier_status_updated").exists()
    assert _client(notaire).patch(f"/api/dossiers/{dossier.reference}", {"statut": "clos"}, format="json").status_code == 200
    assert Notification.objects.filter(type="dossier_status", recipient=clerc).count() == 1


# ===========================================================================
# 8. Sauvegardes et PRA
# ===========================================================================

@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_le_manifeste_en_clair_ne_contient_plus_les_intitules(tmp_path, settings):
    from settings_app.backup_service import run_backup
    settings.BACKUP_LOCAL_ROOT = str(tmp_path)
    notaire, clerc, _c, dossier = _etude()
    _piece("DOC_BK", dossier, clerc)
    run = run_backup(notaire)
    racine = Path(run.local_path) if Path(run.local_path).is_absolute() else Path(settings.BASE_DIR) / run.local_path
    clair = (racine / "manifest.json").read_text(encoding="utf-8")
    assert "DOC_BK" in clair and "Testament_" not in clair and "VEN-2026-00001" not in json.loads(clair)["documents"][0].values()
    assert b"Testament_" not in (racine / "manifest.details.enc").read_bytes()
    assert not any("Testament_" in str(p) for p in racine.rglob("*")), "le nom d'origine n'apparaît plus dans les chemins"
    sortie = io.StringIO()
    call_command("restore_drill", stdout=sortie)
    assert "Test PRA réussi" in sortie.getvalue()
    assert BackupRun.objects.filter(kind="restore_drill", status=BackupRun.Status.SUCCESS).exists()


@pytest.mark.django_db
def test_la_rotation_garde_la_plus_recente_et_ne_sort_jamais_du_depot(tmp_path, settings):
    from settings_app.backup_service import prune_backups
    settings.BACKUP_LOCAL_ROOT = str(tmp_path / "store")
    settings.BACKUP_RETENTION_DAILY, settings.BACKUP_RETENTION_WEEKLY, settings.BACKUP_RETENTION_MONTHLY = 2, 0, 0
    runs = []
    for jours in (0, 1, 2, 3):
        dossier = tmp_path / "store" / f"b{jours}"
        dossier.mkdir(parents=True)
        (dossier / "manifest.json").write_text("{}")
        run = BackupRun.objects.create(status=BackupRun.Status.SUCCESS, kind="backup", local_path=str(dossier))
        BackupRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=jours, hours=1))
        runs.append(run)
    externe = tmp_path / "ailleurs"
    externe.mkdir()
    piege = BackupRun.objects.create(status=BackupRun.Status.SUCCESS, kind="backup", local_path=str(externe))
    BackupRun.objects.filter(pk=piege.pk).update(created_at=timezone.now() - timedelta(days=30))
    prune_backups()
    assert (tmp_path / "store" / "b0").exists() and (tmp_path / "store" / "b1").exists()
    assert not (tmp_path / "store" / "b2").exists() and not (tmp_path / "store" / "b3").exists()
    assert externe.exists(), "la rotation ne supprime jamais hors du dépôt de sauvegardes"


@pytest.mark.django_db
def test_une_sauvegarde_en_retard_declenche_une_alerte_critique(settings):
    from settings_app.tasks import fraicheur_sauvegarde
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    run = BackupRun.objects.create(status=BackupRun.Status.SUCCESS, kind="backup", local_path="x")
    BackupRun.objects.filter(pk=run.pk).update(created_at=timezone.now() - timedelta(days=2))
    fraicheur_sauvegarde()
    fraicheur_sauvegarde()
    alerte = Notification.objects.get(recipient=notaire, type="backup_stale")
    assert alerte.severity == "critique" and alerte.email_status == "en_attente"


# ===========================================================================
# 9. Sécurité et intégrité
# ===========================================================================

@pytest.mark.django_db
def test_la_force_brute_sur_un_compte_leve_une_seule_alerte():
    from audit.tasks import surveiller
    notaire, clerc, _c, _d = _etude()
    anonyme = APIClient()
    for _ in range(5):
        anonyme.post("/api/auth/login", {"email": "clerc@etude.ci", "password": "mauvais"}, format="json")
    surveiller()
    surveiller()
    alerte = SecurityAlert.objects.get(rule="force_brute")
    assert alerte.subject_user == clerc and alerte.status == SecurityAlert.Status.OPEN
    assert Notification.objects.filter(type="security_alert", recipient=notaire).count() == 1

    # Traitement : notaire uniquement, commentaire obligatoire, tracé.
    assert _client(clerc).patch(f"/api/security/alerts/{alerte.pk}", {"status": "traitée", "note": "ok"}, format="json").status_code == 403
    assert _client(notaire).patch(f"/api/security/alerts/{alerte.pk}", {"status": "traitée"}, format="json").status_code == 400
    assert _client(notaire).patch(f"/api/security/alerts/{alerte.pk}", {"status": "traitée", "note": "Le clerc avait oublié son mot de passe."}, format="json").status_code == 200
    assert AuditLog.objects.filter(action="security_alert_handled", user=notaire).exists()
    clerc.refresh_from_db()
    assert clerc.is_active, "la surveillance ne suspend jamais un compte d'elle-même"


@pytest.mark.django_db
@override_settings(**CHIFFREMENT, SECURITY_MASS_VIEW_THRESHOLD=5)
def test_la_consultation_massive_est_detectee():
    from audit.tasks import surveiller
    notaire, clerc, _c, dossier = _etude()
    for i in range(6):
        doc = _piece(f"DOC_M{i}", dossier, notaire)
        _client(clerc).get(f"/api/documents/{doc.reference}")
    surveiller()
    assert SecurityAlert.objects.filter(rule="consultation_massive", subject_user=clerc).count() == 1


@pytest.mark.django_db
def test_une_alteration_du_journal_est_detectee_par_le_controle_quotidien():
    from audit.services import log_system_event
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    for i in range(3):
        log_system_event("evenement", "test", str(i))
    assert run_job("integrite_audit").status == JobRun.Status.SUCCESS
    cible = AuditLog.objects.order_by("id")[1]
    with connection.cursor() as cursor:
        cursor.execute("UPDATE audit_auditlog SET action = %s WHERE id = %s", ["falsifie", cible.pk])
    run = run_job("integrite_audit")
    assert run.status == JobRun.Status.FAILURE
    assert SecurityAlert.objects.filter(rule="integrite_audit", severity="critique").exists()
    assert Notification.objects.filter(recipient=notaire, type="security_alert").exists()


# ===========================================================================
# 10. Nettoyage : liste blanche stricte
# ===========================================================================

@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_le_nettoyage_ne_touche_ni_aux_documents_ni_au_journal():
    notaire, clerc, _c, dossier = _etude()
    _piece("DOC_KEEP", dossier, clerc)
    OTPCode.objects.create(email="x@x.ci", code_hash="h", purpose="login_mfa", expires_at=timezone.now() - timedelta(days=3))
    vieille = Notification.objects.create(recipient=clerc, type="info", message="m", read=True)
    Notification.objects.filter(pk=vieille.pk).update(created_at=timezone.now() - timedelta(days=400))
    journal = AuditLog.objects.count()
    run = run_job("nettoyage")
    assert run.status == JobRun.Status.SUCCESS
    assert not OTPCode.objects.exists() and not Notification.objects.filter(pk=vieille.pk).exists()
    assert Document.objects.filter(reference="DOC_KEEP").exists()
    assert AuditLog.objects.count() >= journal


# ===========================================================================
# 11. Cloche : interrogeable périodiquement sans limitation
# ===========================================================================

@pytest.mark.django_db
def test_le_compteur_de_la_cloche_n_est_pas_limite():
    notaire, clerc, _c, _d = _etude()
    c = _client(clerc)
    codes = {c.get("/api/notifications/unread-count").status_code for _ in range(30)}
    assert codes == {200}
