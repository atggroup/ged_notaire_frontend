import base64
from datetime import timedelta
import pytest
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from accounts.models import InviteCode, OTPCode, User
from audit.models import AuditLog
from documents.models import Document
from dossiers.models import Dossier
from notifications.models import Notification
from settings_app.models import BackupRun

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_login_returns_front_contract():
    user = User.objects.create_user(email="notaire@test.ci", password="Secret123", role="admin")
    response = APIClient().post("/api/auth/login", {"email": user.email, "password": "Secret123"}, format="json")
    assert response.status_code == 202
    assert response.data["mfaRequired"] is True
    OTPCode.objects.create(email=user.email, purpose="login_mfa", code_hash=make_password("123456"), expires_at=timezone.now() + timedelta(minutes=5))
    response = APIClient().post("/api/auth/mfa/verify", {"email": user.email, "code": "123456"}, format="json")
    assert response.status_code == 200
    assert set(("token", "role", "name", "email")) <= response.data.keys()
    assert response.data["role"] == "admin"
    assert AuditLog.objects.filter(action="login_mfa").exists()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_invited_registration_creates_the_role_authorised_by_notary():
    admin = User.objects.create_user(email="notaire@test.ci", password="Secret123", role="admin")
    invite = InviteCode.objects.create(code="invitation-test-code-12345", email="awa@test.ci", role="clerc", expires_at=timezone.now() + timedelta(days=1), created_by=admin)
    client = APIClient()
    start = client.post("/api/auth/register/start", {"role": "clerc", "inviteCode": invite.code, "firstName": "Awa", "lastName": "Kone", "email": "awa@test.ci", "phone": "", "jobTitle": "Clerc"}, format="json")
    assert start.status_code == 200
    OTPCode.objects.create(email="awa@test.ci", purpose="register", code_hash=make_password("123456"), expires_at=timezone.now() + timedelta(minutes=5))
    assert client.post("/api/auth/register/verify-code", {"email": "awa@test.ci", "code": "123456"}, format="json").status_code == 200
    completed = client.post("/api/auth/register/complete", {"email": "awa@test.ci", "password": "Secure123"}, format="json")
    assert completed.status_code == 201
    assert completed.data["role"] == "clerc"
    assert User.objects.get(email="awa@test.ci").role == "clerc"
    invite.refresh_from_db()
    assert invite.used_at is not None


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_registration_requires_a_matching_invitation():
    response = APIClient().post("/api/auth/register/start", {"role": "clerc", "inviteCode": "not-authorised", "firstName": "X", "lastName": "Y", "email": "wannabe-clerc@test.ci"}, format="json")
    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_invitation_cannot_be_reused_for_a_different_role_or_email():
    admin = User.objects.create_user(email="notaire@test.ci", password="Secret123", role="admin")
    invite = InviteCode.objects.create(code="invitation-test-code-67890", email="a@test.ci", role="collaborateur", expires_at=timezone.now() + timedelta(days=1), created_by=admin)
    response = APIClient().post("/api/auth/register/start", {"role": "admin", "inviteCode": invite.code, "firstName": "X", "lastName": "Y", "email": "wannabe-admin@test.ci"}, format="json")
    assert response.status_code == 400
    assert not User.objects.filter(email="wannabe-admin@test.ci").exists()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_upload_is_encrypted_and_confidential_document_is_hidden():
    admin = User.objects.create_user(email="admin@test.ci", password="Secret123", role="admin")
    collaborator = User.objects.create_user(email="collab@test.ci", password="Secret123", role="collaborateur")
    client = APIClient(); client.force_authenticate(admin)
    dossier = Dossier.objects.create(reference="VEN-2026-00001", domaine="VEN", nom="Vente confidentielle", client="Mme Awa", niveau_de_confidentialite="Confidentiel", created_by=admin)
    file = SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0" + b"document sensible", content_type="image/jpeg")
    response = client.post("/api/documents", {"file": file, "type": "Vente", "dossier": dossier.reference, "niveau": "Confidentiel"}, format="multipart")
    assert response.status_code == 201
    document = Document.objects.get(reference=response.data["reference"])
    with document.fichier.open("rb") as stored:
        assert b"document sensible" not in stored.read()
    client.force_authenticate(collaborator)
    assert client.get("/api/documents").data == []
    assert client.get(f"/api/documents/{document.reference}?format=json").status_code == 403
    assert AuditLog.objects.filter(action="document_uploaded", target_id=document.reference).exists()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_scan_requires_an_existing_dossier_and_cannot_set_confidentiality():
    clerc = User.objects.create_user(email="clerc@test.ci", password="Secret123", role="clerc")
    client = APIClient(); client.force_authenticate(clerc)
    response = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("piece.jpg", b"\xff\xd8\xff\xe0x", content_type="image/jpeg"), "type": "Vente", "client": "Mme Kouamé Awa"}, format="multipart")
    assert response.status_code == 400
    folder = Dossier.objects.create(reference="VEN-2026-00001", domaine="VEN", nom="Vente Awa", client="Mme Kouamé Awa", niveau_de_confidentialite="Restreint", created_by=clerc)
    response = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("piece.jpg", b"\xff\xd8\xff\xe0x", content_type="image/jpeg"), "type": "Vente", "dossier": folder.reference, "niveau": "Confidentiel"}, format="multipart")
    assert response.status_code == 201
    assert response.data["reference"].startswith("DOC_")
    assert response.data["niveau_de_confidentialite"] == "Restreint"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_profile_and_backup_pages_use_persisted_api_data():
    admin = User.objects.create_user(email="admin@test.ci", password="Secret123", role="admin", first_name="Ancien", last_name="Nom")
    client = APIClient(); client.force_authenticate(admin)

    profile = client.patch("/api/me", {"name": "Nouveau Nom"}, format="json")
    assert profile.status_code == 200
    admin.refresh_from_db()
    assert admin.display_name == "Nouveau Nom"

    status_response = client.get("/api/backups")
    assert status_response.status_code == 200
    assert status_response.data["local"]["documentCount"] == 0
    assert status_response.data["cloud"]["configured"] is False

    check = client.post("/api/backups/restore-test", {}, format="json")
    assert check.status_code == 200
    assert check.data["status"] == "success"
    assert BackupRun.objects.filter(status="success", requested_by=admin).exists()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_document_governance_and_dossier_operations():
    admin = User.objects.create_user(email="notaire@cabinet.ci", password="Secret123", role="admin")
    clerc = User.objects.create_user(email="clerc@cabinet.ci", password="Secret123", role="clerc")
    dossier = Dossier.objects.create(reference="VEN-2026-00042", domaine="VEN", nom="Vente A", client="Mme Awa", niveau_de_confidentialite="Confidentiel", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)
    created = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0" + b"scan", content_type="image/jpeg"), "type": "Vente", "dossier": dossier.reference, "niveau": "Confidentiel"}, format="multipart")
    assert created.status_code == 201
    ref = created.data["reference"]
    assert client.post("/api/documents/%s/trash" % ref, {}, format="json").status_code == 200
    assert client.post("/api/documents/%s/request-destruction" % ref, {}, format="json").status_code == 200
    assert client.post("/api/documents/%s/restore" % ref, {}, format="json").status_code == 200
    saved = client.post("/api/searches/saved", {"name": "Ventes Awa", "filters": {"client": "Awa", "niveau": "Confidentiel"}}, format="json")
    assert saved.status_code == 201
    assignment = client.post("/api/dossiers/%s/assignments" % dossier.reference, {"userId": clerc.id, "role": "clerc_responsable"}, format="json")
    assert assignment.status_code == 201
    checklist = client.post("/api/dossiers/%s/checklist" % dossier.reference, {"label": "Pièce d'identité", "required": True}, format="json")
    assert checklist.status_code == 201
    assert client.patch("/api/dossiers/%s/checklist" % dossier.reference, {"id": checklist.data["id"], "completed": True}, format="json").status_code == 200
    physical = client.post("/api/dossiers/%s/physical-records" % dossier.reference, {"room": "Archives", "cabinet": "A03", "shelf": "02", "box": "018", "folder": "004", "document": ref}, format="json")
    assert physical.status_code == 201
    assert client.get("/api/dossiers/%s/export" % dossier.reference).status_code == 200
    assert client.get("/api/audit/integrity").data["ok"] is True
    client.force_authenticate(clerc)
    assert client.get("/api/documents/%s?format=json" % ref).status_code == 200


@pytest.mark.django_db
def test_patch_dossier_detail_updates_statut_and_legal_hold():
    """Régression : le frontend appelle PATCH /api/dossiers/<reference>
    (route détail), pas PATCH /api/dossiers (route liste). Les deux routes
    sont servies par des vues différentes ; avant ce test, la logique de
    mise à jour vivait par erreur sur la route liste et cette route
    détail renvoyait 405 pour toute tentative de PATCH."""
    admin = User.objects.create_user(email="notaire2@cabinet.ci", password="Secret123", role="admin")
    dossier = Dossier.objects.create(reference="VEN-2026-00099", domaine="VEN", nom="Vente B", client="M. Koffi", niveau_de_confidentialite="Restreint", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)

    advance = client.patch("/api/dossiers/%s" % dossier.reference, {"reference": dossier.reference, "statut": "en_instruction"}, format="json")
    assert advance.status_code == 200
    dossier.refresh_from_db()
    assert dossier.statut == "en_instruction"

    freeze = client.patch("/api/dossiers/%s" % dossier.reference, {"reference": dossier.reference, "legalHold": True, "legalHoldReason": "Litige en cours"}, format="json")
    assert freeze.status_code == 200
    dossier.refresh_from_db()
    assert dossier.legal_hold is True
    assert dossier.legal_hold_reason == "Litige en cours"

    unfreeze = client.patch("/api/dossiers/%s" % dossier.reference, {"reference": dossier.reference, "legalHold": False}, format="json")
    assert unfreeze.status_code == 200
    dossier.refresh_from_db()
    assert dossier.legal_hold is False


@pytest.mark.django_db
def test_dossier_assignment_notifies_the_assigned_user():
    """Régression : affecter un dossier à un clerc/collaborateur créait bien
    l'affectation (accès correct) mais ne prévenait jamais la personne —
    elle ne le découvrait que si elle pensait à vérifier elle-même. Une
    notification doit être envoyée à la création de l'affectation."""
    admin = User.objects.create_user(email="notaire3@cabinet.ci", password="Secret123", role="admin")
    clerc = User.objects.create_user(email="clerc3@cabinet.ci", password="Secret123", role="clerc")
    dossier = Dossier.objects.create(reference="VEN-2026-00077", domaine="VEN", nom="Vente C", client="Mme Test", niveau_de_confidentialite="Restreint", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)

    assert Notification.objects.filter(recipient=clerc).count() == 0
    created = client.post("/api/dossiers/%s/assignments" % dossier.reference, {"userId": clerc.id, "role": "clerc_responsable"}, format="json")
    assert created.status_code == 201

    notifs = Notification.objects.filter(recipient=clerc)
    assert notifs.count() == 1
    assert dossier.reference in notifs.first().message

    # Ré-affecter la même personne au même rôle (mise à jour, pas création) ne doit pas spammer une 2e notif.
    updated = client.post("/api/dossiers/%s/assignments" % dossier.reference, {"userId": clerc.id, "role": "clerc_responsable", "dueAt": "2026-12-01T00:00:00Z"}, format="json")
    assert updated.status_code == 200
    assert Notification.objects.filter(recipient=clerc).count() == 1


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_dossier_detail_includes_filtered_documents_and_tasks():
    """Régression : GET /dossiers/<ref> ne renvoyait jamais les clés
    'documents' et 'tasks' — les onglets Documents/Tâches de la page dossier
    étaient donc toujours vides quel que soit le contenu réel. Vérifie aussi
    que le filtrage de confidentialité documentaire s'applique à cet endroit
    précis (un clerc assigné ne doit pas voir un document 'Très
    confidentiel' du dossier sans permission explicite dessus)."""
    admin = User.objects.create_user(email="notaire4@cabinet.ci", password="Secret123", role="admin")
    clerc = User.objects.create_user(email="clerc4@cabinet.ci", password="Secret123", role="clerc")
    dossier = Dossier.objects.create(reference="VEN-2026-00088", domaine="VEN", nom="Vente D", client="M. Test", niveau_de_confidentialite="Restreint", created_by=admin)
    from dossiers.models import DossierAssignment
    DossierAssignment.objects.create(dossier=dossier, user=clerc, role=DossierAssignment.Role.CLERK, assigned_by=admin)

    admin_client = APIClient(); admin_client.force_authenticate(admin)
    standard_file = SimpleUploadedFile("standard.pdf", b"%PDF-1.4 standard", content_type="application/pdf")
    secret_file = SimpleUploadedFile("secret.pdf", b"%PDF-1.4 secret", content_type="application/pdf")
    r1 = admin_client.post("/api/documents", {"file": standard_file, "type": "Vente", "dossier": dossier.reference, "niveau": "Standard"}, format="multipart")
    assert r1.status_code == 201
    r2 = admin_client.post("/api/documents", {"file": secret_file, "type": "Vente", "dossier": dossier.reference, "niveau": "Très confidentiel"}, format="multipart")
    assert r2.status_code == 201

    Task = Notification._meta.apps.get_model("notifications", "Task")
    Task.objects.create(title="Vérifier pièces", assigned_to=clerc, assigned_by=admin, dossier=dossier)

    admin_detail = admin_client.get(f"/api/dossiers/{dossier.reference}")
    assert admin_detail.status_code == 200
    assert len(admin_detail.data["documents"]) == 2
    assert len(admin_detail.data["tasks"]) == 1

    clerc_client = APIClient(); clerc_client.force_authenticate(clerc)
    clerc_detail = clerc_client.get(f"/api/dossiers/{dossier.reference}")
    assert clerc_detail.status_code == 200
    # Le clerc voit le dossier (affecté) mais seulement le document standard :
    # le "Très confidentiel" exige une permission explicite, distincte de
    # l'affectation au dossier (voir permissions_app.access.has_document_access).
    assert len(clerc_detail.data["documents"]) == 1
    assert clerc_detail.data["documents"][0]["niveau_de_confidentialite"] == "Standard"
    assert len(clerc_detail.data["tasks"]) == 1


KEY_Q1 = base64.urlsafe_b64encode(b"11111111111111111111111111111111").decode()
KEY_Q3 = base64.urlsafe_b64encode(b"33333333333333333333333333333333").decode()


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEYS='{"2026-q1": "%s"}' % KEY_Q1, DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="2026-q1", DOCUMENT_ENCRYPTION_KEY="")
def test_key_rotation_keeps_old_documents_readable_after_switching_active_key():
    """A document encrypted before a key rotation must remain readable
    after the active key changes -- this is the whole point of keeping a
    per-document key_id instead of a single global key."""
    admin = User.objects.create_user(email="notaire2@cabinet.ci", password="Secret123", role="admin")
    dossier = Dossier.objects.create(reference="VEN-2026-00099", domaine="VEN", nom="Vente B", client="M. Koffi", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)
    created = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0" + b"contenu-q1", content_type="image/jpeg"), "type": "Vente", "dossier": dossier.reference}, format="multipart")
    assert created.status_code == 201
    ref = created.data["reference"]
    doc = Document.objects.get(reference=ref)
    assert doc.encryption_key_id == "2026-q1"

    # Rotate: the old key stays in the ring (never delete a key still in
    # use), a new one becomes active for future uploads.
    with override_settings(DOCUMENT_ENCRYPTION_KEYS='{"2026-q1": "%s", "2026-q3": "%s"}' % (KEY_Q1, KEY_Q3), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="2026-q3"):
        # The document uploaded under the old key must still open normally.
        assert client.get("/api/documents/%s" % ref).status_code == 200
        new_upload = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte2.jpg", b"\xff\xd8\xff\xe0" + b"contenu-q3", content_type="image/jpeg"), "type": "Vente", "dossier": dossier.reference}, format="multipart")
        assert new_upload.status_code == 201
        assert Document.objects.get(reference=new_upload.data["reference"]).encryption_key_id == "2026-q3"
        summary = client.get("/api/dashboard/summary")
        assert summary.data["chiffrement"]["activeKeyId"] == "2026-q3"
        assert set(summary.data["chiffrement"]["knownKeyIds"]) == {"2026-q1", "2026-q3"}


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_admin_dashboard_surfaces_sensitive_and_blocked_dossiers():
    admin = User.objects.create_user(email="notaire3@cabinet.ci", password="Secret123", role="admin")
    Dossier.objects.create(reference="SUC-2026-00001", domaine="SUC", nom="Succession Kouassi", client="Famille Kouassi", niveau_de_confidentialite="Confidentiel", created_by=admin)
    frozen = Dossier.objects.create(reference="SUC-2026-00002", domaine="SUC", nom="Succession gelee", client="Famille Yao", statut="en_instruction", legal_hold=True, legal_hold_reason="Contentieux en cours", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)
    summary = client.get("/api/dashboard/summary")
    assert summary.status_code == 200
    assert summary.data["dossiersSensiblesCount"] >= 1
    assert summary.data["dossiersBloquesCount"] == 1
    assert summary.data["dossiersBloques"][0]["reference"] == frozen.reference
    assert "alertesSecurite" in summary.data


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, BACKUP_CLOUD_ENABLED=False)
def test_real_backup_creates_encrypted_copy_and_manifest(tmp_path, settings):
    admin = User.objects.create_user(email="backup@test.ci", password="Secret123", role="admin")
    dossier = Dossier.objects.create(reference="VEN-2026-00111", domaine="VEN", nom="Vente backup", client="Mme Awa", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)
    created = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0backup", content_type="image/jpeg"), "type": "Vente", "dossier": dossier.reference}, format="multipart")
    assert created.status_code == 201
    settings.BACKUP_LOCAL_ROOT = str(tmp_path / "backup")
    response = client.post("/api/backups/run", {}, format="json")
    assert response.status_code == 200
    run = BackupRun.objects.get(pk=response.data["run"]["id"])
    assert run.kind == "backup"
    assert run.status == "success"
    manifest = list((tmp_path / "backup").rglob("manifest.json"))
    assert manifest
    assert created.data["reference"] in manifest[0].read_text(encoding="utf-8")


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_destroy_requires_authorization_and_removes_binary(tmp_path):
    admin = User.objects.create_user(email="destroy@test.ci", password="Secret123", role="admin")
    dossier = Dossier.objects.create(reference="VEN-2026-00112", domaine="VEN", nom="Vente destruction", client="Mme Awa", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)
    created = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0destroy", content_type="image/jpeg"), "type": "Vente", "dossier": dossier.reference}, format="multipart")
    assert created.status_code == 201
    ref = created.data["reference"]
    doc = Document.objects.get(reference=ref)
    stored_name = doc.fichier.name
    assert client.post(f"/api/documents/{ref}/destroy", {}, format="json").status_code == 409
    assert client.post(f"/api/documents/{ref}/trash", {}, format="json").status_code == 200
    assert client.post(f"/api/documents/{ref}/request-destruction", {}, format="json").status_code == 200
    assert client.post(f"/api/documents/{ref}/authorize-destruction", {}, format="json").status_code == 200
    assert client.post(f"/api/documents/{ref}/destroy", {}, format="json").status_code == 200
    doc.refresh_from_db()
    assert doc.statut == Document.Status.DESTROYED
    assert doc.fichier.name == ""
    assert not doc.fichier.storage.exists(stored_name)
    assert AuditLog.objects.filter(action="document_destroyed", target_id=ref).exists()


@pytest.mark.django_db
def test_deactivation_records_departure_and_revokes_sessions():
    admin = User.objects.create_user(email="admin2@test.ci", password="Secret123", role="admin")
    user = User.objects.create_user(email="depart@test.ci", password="Secret123", role="collaborateur")
    client = APIClient(); client.force_authenticate(admin)
    response = client.patch("/api/users", {"id": user.id, "isActive": False, "departureReason": "Fin de collaboration"}, format="json")
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_active is False and user.deactivated_at is not None
    assert user.deactivated_by_id == admin.id and user.departure_reason == "Fin de collaboration"

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_legal_hold_blocks_every_sensitive_action_on_a_frozen_dossier():
    """Matrice de gel : une fois legal_hold=True, AUCUNE des actions qui
    modifient, versionnent ou détruisent un document du dossier ne doit
    passer -- ni celles qui ajoutent du contenu au dossier gelé."""
    admin = User.objects.create_user(email="gel@cabinet.ci", password="Secret123", role="admin")
    dossier = Dossier.objects.create(reference="SUC-2026-00050", domaine="SUC", nom="Succession gelée", client="Famille Kone", created_by=admin)
    client = APIClient(); client.force_authenticate(admin)

    # Le document est déposé AVANT le gel (le dépôt initial doit encore
    # marcher tant que le dossier n'est pas gelé).
    created = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0avant-gel", content_type="image/jpeg"), "type": "Succession", "dossier": dossier.reference}, format="multipart")
    assert created.status_code == 201
    ref = created.data["reference"]

    dossier.legal_hold = True
    dossier.legal_hold_reason = "Contentieux successoral en cours"
    dossier.save(update_fields=["legal_hold", "legal_hold_reason"])

    # 1) Dépôt d'un nouveau document dans le dossier gelé : bloqué.
    blocked_upload = client.post("/api/documents/upload", {"fichier": SimpleUploadedFile("piece2.jpg", b"\xff\xd8\xff\xe0apres-gel", content_type="image/jpeg"), "type": "Succession", "dossier": dossier.reference}, format="multipart")
    assert blocked_upload.status_code == 409

    # 2) Nouvelle version d'un document existant du dossier gelé : bloqué.
    blocked_version = client.post(f"/api/documents/{ref}/versions", {"fichier": SimpleUploadedFile("acte-v2.jpg", b"\xff\xd8\xff\xe0v2", content_type="image/jpeg")}, format="multipart")
    assert blocked_version.status_code == 409

    # 3) Changement de confidentialité du document : bloqué.
    blocked_patch = client.patch(f"/api/documents/{ref}", {"niveau_de_confidentialite": "Confidentiel"}, format="json")
    assert blocked_patch.status_code == 409

    # 4) Archivage : bloqué.
    blocked_archive = client.post("/api/documents/archive", {"reference": ref}, format="json")
    assert blocked_archive.status_code == 409

    # 5-8) Toute la chaîne corbeille/destruction : bloquée à chaque étape.
    for action in ("trash", "restore", "request-destruction", "authorize-destruction", "destroy"):
        blocked = client.post(f"/api/documents/{ref}/{action}", {}, format="json")
        assert blocked.status_code == 409, f"l'action '{action}' n'est pas bloquée par le gel du dossier"

    # Le document original n'a subi aucune altération.
    doc = Document.objects.get(reference=ref)
    assert doc.statut == Document.Status.TO_INDEX
    assert doc.niveau_de_confidentialite == Document.Confidentiality.STANDARD
    assert doc.fichier.name != ""


@pytest.mark.django_db
def test_very_confidential_requires_explicit_permission_even_when_assigned():
    admin = User.objects.create_user(email="admin3@test.ci", password="Secret123", role="admin")
    collab = User.objects.create_user(email="vc@test.ci", password="Secret123", role="collaborateur")
    dossier = Dossier.objects.create(reference="VEN-2026-00009", domaine="VEN", nom="Très confidentiel", client="X", niveau_de_confidentialite="Très confidentiel", created_by=admin)
    from dossiers.models import DossierAssignment
    DossierAssignment.objects.create(dossier=dossier, user=collab, role=DossierAssignment.Role.COLLABORATOR, assigned_by=admin)
    file = SimpleUploadedFile("acte.jpg", b"\xff\xd8\xff\xe0secret", content_type="image/jpeg")
    client=APIClient(); client.force_authenticate(admin)
    created=client.post("/api/documents", {"file":file,"type":"Vente","dossier":dossier.reference,"niveau":"Très confidentiel"}, format="multipart")
    assert created.status_code==201
    client.force_authenticate(collab)
    assert client.get(f"/api/documents/{created.data['reference']}?format=json").status_code==403
