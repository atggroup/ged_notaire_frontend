"""Garde-fous de sécurité.

Chaque test correspond à une faiblesse constatée puis corrigée.
"""
import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from django.core.files.base import ContentFile
from django.test import Client, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from documents.crypto import decrypt, encrypt
from documents.models import Document
from dossiers.models import Dossier, DossierAssignment

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
PDF = b"%PDF-1.4\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _cache_propre():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _piece(reference, dossier, notaire, niveau="Très confidentiel"):
    chiffre, key_id = encrypt(PDF)
    doc = Document.objects.create(
        reference=reference, dossier=dossier, type="Acte", nom="acte.pdf",
        original_filename="acte.pdf", content_type="application/pdf",
        size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(),
        uploaded_by=notaire, master_reference=reference, encryption_key_id=key_id,
        niveau_de_confidentialite=niveau,
        statut=Document.Status.VALIDATED, quality_passed=True,
    )
    doc.fichier.save("acte.pdf.enc", ContentFile(chiffre), save=True)
    return doc


# --------------------------------------------------------------------------
# 1. Le MFA est réévalué quand les droits changent
# --------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_une_habilitation_confidentielle_revoque_une_session_sans_mfa():
    """Une session ouverte par simple mot de passe ne doit pas gagner en
    silence l'accès à du confidentiel : la politique MFA serait contournée
    pendant toute la durée de vie du jeton."""
    notaire = User.objects.create_user(email="n@x.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c@x.ci", password="Mdp!123456", role="clerc")
    dossier = Dossier.objects.create(
        reference="SUC-2026-00001", domaine="SUC", nom="D", client="C",
        created_by=notaire, niveau_de_confidentialite="Très confidentiel")
    doc = _piece("DOC_MFA", dossier, notaire)

    # Le clerc n'a encore aucun accès confidentiel : pas de second facteur.
    connexion = Client().post("/api/auth/login",
                              {"email": "c@x.ci", "password": "Mdp!123456"},
                              content_type="application/json")
    assert connexion.status_code == 200, "sans accès confidentiel, le MFA ne s'applique pas"
    jeton = connexion.json()["token"]
    clerc.refresh_from_db()
    version_avant = clerc.session_version

    # Le notaire lui ouvre la pièce.
    admin_client = APIClient()
    admin_client.force_authenticate(notaire)
    octroi = admin_client.post("/api/permissions", {
        "email": clerc.email, "ref": doc.reference,
        "accessLevel": "lecture", "motif": "Dossier confié",
    }, format="json")
    assert octroi.status_code == 201

    clerc.refresh_from_db()
    assert clerc.session_version == version_avant + 1, "la session doit être révoquée"

    ancienne_session = Client(HTTP_AUTHORIZATION="Bearer " + jeton)
    assert ancienne_session.get(f"/api/documents/{doc.reference}?format=json").status_code == 401

    # Et la reconnexion exige désormais le second facteur.
    suivante = Client().post("/api/auth/login",
                             {"email": "c@x.ci", "password": "Mdp!123456"},
                             content_type="application/json")
    assert suivante.status_code == 202
    assert suivante.json()["mfaRequired"] is True


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_une_affectation_confidentielle_revoque_aussi():
    notaire = User.objects.create_user(email="n2@x.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c2@x.ci", password="Mdp!123456", role="clerc")
    dossier = Dossier.objects.create(
        reference="SUC-2026-00002", domaine="SUC", nom="D", client="C",
        created_by=notaire, niveau_de_confidentialite="Confidentiel")

    Client().post("/api/auth/login", {"email": "c2@x.ci", "password": "Mdp!123456"},
                  content_type="application/json")
    clerc.refresh_from_db()
    version_avant = clerc.session_version

    admin_client = APIClient()
    admin_client.force_authenticate(notaire)
    r = admin_client.post(f"/api/dossiers/{dossier.reference}/assignments",
                          {"userId": clerc.id, "role": DossierAssignment.Role.COLLABORATOR},
                          format="json")
    assert r.status_code == 201
    clerc.refresh_from_db()
    assert clerc.session_version == version_avant + 1


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_une_habilitation_standard_ne_deconnecte_personne():
    """La révocation ne doit pas devenir une gêne quotidienne : seul le
    franchissement du seuil confidentiel la déclenche."""
    notaire = User.objects.create_user(email="n3@x.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c3@x.ci", password="Mdp!123456", role="clerc")
    dossier = Dossier.objects.create(
        reference="AUT-2026-00003", domaine="AUT", nom="D", client="C",
        created_by=notaire, niveau_de_confidentialite="Standard")
    doc = _piece("DOC_STD", dossier, notaire, niveau="Standard")

    Client().post("/api/auth/login", {"email": "c3@x.ci", "password": "Mdp!123456"},
                  content_type="application/json")
    clerc.refresh_from_db()
    version_avant = clerc.session_version

    admin_client = APIClient()
    admin_client.force_authenticate(notaire)
    admin_client.post("/api/permissions", {
        "email": clerc.email, "ref": doc.reference,
        "accessLevel": "lecture", "motif": "Ordinaire",
    }, format="json")

    clerc.refresh_from_db()
    assert clerc.session_version == version_avant


# --------------------------------------------------------------------------
# 2. La sauvegarde protège aussi les métadonnées
# --------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_la_sauvegarde_chiffre_l_instantane_de_la_base(tmp_path, settings):
    """En étude notariale, l'intitulé seul d'un dossier relève du secret : un
    export en clair livrerait noms de dossiers, journal d'audit et empreintes
    de mots de passe à qui met la main sur le support."""
    settings.BACKUP_LOCAL_ROOT = str(tmp_path)
    from settings_app.backup_service import run_backup

    notaire = User.objects.create_user(email="n4@x.ci", password="Mdp!123456", role="admin")
    dossier = Dossier.objects.create(
        reference="SUC-2026-00004", domaine="SUC",
        nom="Succession Famille Temoin", client="Famille Temoin",
        created_by=notaire, niveau_de_confidentialite="Très confidentiel")
    _piece("DOC_BKP", dossier, notaire)

    run = run_backup(notaire)
    assert run.status == "succes" or run.checked_documents == 1

    racine = Path(run.local_path)
    if not racine.is_absolute():
        racine = Path(settings.BASE_DIR) / run.local_path
    manifeste = json.loads((racine / "manifest.json").read_text(encoding="utf-8"))
    entree = manifeste["database"]

    assert entree["encrypted"] is True
    assert entree["file"] == "database.json.enc"
    assert "accounts.otpcode" in entree["excludedTables"]

    octets = (racine / entree["file"]).read_bytes()
    for marqueur in (b"Succession Famille Temoin", b"Famille Temoin", b"argon2$", b"n4@x.ci"):
        assert marqueur not in octets, f"{marqueur!r} lisible dans la sauvegarde"

    # …mais la sauvegarde reste restaurable.
    clair = decrypt(octets, entree["encryptionKeyId"])
    assert hashlib.sha256(clair).hexdigest() == entree["plaintextSha256"]
    donnees = json.loads(clair.decode("utf-8"))
    # Format GED-DB-2 : objets `dumpdata` + journal d'audit exporté à part.
    assert donnees["format"] == "GED-DB-2" and isinstance(donnees["audit"], list)
    tables = {o["model"] for o in donnees["objects"]}
    assert "accounts.user" in tables
    assert "accounts.otpcode" not in tables, "les codes MFA n'ont rien à faire dans une sauvegarde"


# --------------------------------------------------------------------------
# 3. En-têtes de sécurité
# --------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_reponse_porte_une_csp_stricte_sur_les_scripts():
    reponse = Client().get("/api/health")
    csp = reponse["Content-Security-Policy"]
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp.split("script-src 'self'")[1].split(";")[0], (
        "script-src ne doit jamais autoriser l'inline : c'est la seule directive "
        "qui protège réellement contre une injection"
    )
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert reponse["Permissions-Policy"].startswith("camera=()")


@pytest.mark.django_db
def test_les_pages_ne_contiennent_plus_de_script_en_ligne():
    """La CSP stricte n'a de sens que si l'interface s'en passe réellement."""
    racine = Path(__file__).resolve().parent.parent.parent
    fautives = []
    for page in list(racine.glob("*.html")) + list(racine.glob("*/*.html")):
        texte = page.read_text(encoding="utf-8")
        if "<script>" in texte:
            fautives.append(f"{page.name} : balise <script> en ligne")
        for handler in ("onclick=", "onerror=", "onload=", "onchange=", "onsubmit="):
            if handler in texte:
                fautives.append(f"{page.name} : attribut {handler}")
    assert not fautives, fautives


@pytest.mark.django_db
def test_pdfjs_est_servi_par_l_application_et_non_par_un_cdn():
    """Une étude ne doit dépendre ni d'un CDN tiers ni d'un accès Internet
    pour consulter ses propres actes."""
    racine = Path(__file__).resolve().parent.parent.parent
    assert (racine / "assets" / "vendor" / "pdf.min.js").is_file()
    assert (racine / "assets" / "vendor" / "pdf.worker.min.js").is_file()
    for page in racine.glob("*/document-detail.html"):
        assert "cdnjs" not in page.read_text(encoding="utf-8")


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY)
def test_le_rodage_de_restauration_lit_l_instantane_chiffre(tmp_path, settings):
    """La voie planifiée (sidecar `backup` du docker-compose) passe par la
    commande `restore_drill`, pas par l'API : elle doit suivre le chiffrement
    de l'instantané, sinon le test PRA échoue toutes les nuits sans que
    personne ne comprenne pourquoi."""
    from django.core.management import call_command

    settings.BACKUP_LOCAL_ROOT = str(tmp_path)
    notaire = User.objects.create_user(email="n5@x.ci", password="Mdp!123456", role="admin")
    dossier = Dossier.objects.create(
        reference="AUT-2026-00005", domaine="AUT", nom="D", client="C",
        created_by=notaire, niveau_de_confidentialite="Standard")
    _piece("DOC_DRILL", dossier, notaire, niveau="Standard")

    call_command("backup_ged")
    sortie = io.StringIO()
    call_command("restore_drill", stdout=sortie)
    assert "Test PRA réussi" in sortie.getvalue()
    assert "1 document(s) vérifié(s)" in sortie.getvalue()


# --------------------------------------------------------------------------
# 4. Changement de rôle
# --------------------------------------------------------------------------

def _notaire_et_cible():
    notaire = User.objects.create_user(email="n6@x.ci", password="Mdp!123456", role="admin")
    cible = User.objects.create_user(email="c6@x.ci", password="Mdp!123456", role="collaborateur")
    cible.can_scan = True
    cible.save()
    client = APIClient()
    client.force_authenticate(notaire)
    return notaire, cible, client


@pytest.mark.django_db
def test_le_changement_de_role_exige_un_motif():
    """Une modification de privilèges doit rester justifiable a posteriori."""
    _, cible, client = _notaire_et_cible()
    r = client.patch("/api/users", {"id": cible.pk, "role": "clerc"}, format="json")
    assert r.status_code == 400
    assert "motif" in r.data
    cible.refresh_from_db()
    assert cible.role == "collaborateur"


@pytest.mark.django_db
def test_un_notaire_ne_modifie_pas_son_propre_role():
    """Auto-rétrogradation : le notaire se couperait l'accès aux comptes,
    aux clés et à la validation, sans personne pour le rétablir."""
    notaire, _, client = _notaire_et_cible()
    r = client.patch("/api/users", {"id": notaire.pk, "role": "clerc", "motif": "test"},
                     format="json")
    assert r.status_code == 409
    notaire.refresh_from_db()
    assert notaire.role == "admin"


@pytest.mark.django_db
def test_un_role_inconnu_est_refuse():
    _, cible, client = _notaire_et_cible()
    r = client.patch("/api/users", {"id": cible.pk, "role": "directeur", "motif": "x"},
                     format="json")
    assert r.status_code == 400


@pytest.mark.django_db
def test_le_changement_de_role_revoque_les_sessions_et_se_trace():
    notaire, cible, client = _notaire_et_cible()
    version_avant = cible.session_version

    r = client.patch("/api/users",
                     {"id": cible.pk, "role": "clerc", "motif": "Promotion au poste de clerc."},
                     format="json")
    assert r.status_code == 200
    assert r.data["role"] == "clerc"

    cible.refresh_from_db()
    assert cible.role == "clerc"
    # Les sessions portaient les anciens privilèges.
    assert cible.session_version == version_avant + 1
    assert cible.session_mfa_verified is False
    # `can_scan` ne concerne que les collaborateurs : il ne doit pas resurgir
    # en silence lors d'un retour à ce rôle.
    assert cible.can_scan is False

    trace = AuditLog.objects.filter(action="user_role_changed", target_id=str(cible.pk)).first()
    assert trace is not None, "le changement de rôle doit être journalisé"
    # Le cahier des charges demande l'ancienne ET la nouvelle valeur.
    assert trace.metadata["previous"] == "collaborateur"
    assert trace.metadata["next"] == "clerc"
    assert "Promotion" in trace.metadata["motif"]
    assert trace.user_id == notaire.pk


@pytest.mark.django_db
def test_l_interesse_est_prevenu_du_changement():
    from notifications.models import Notification
    _, cible, client = _notaire_et_cible()
    client.patch("/api/users", {"id": cible.pk, "role": "clerc", "motif": "Promotion."},
                 format="json")
    n = Notification.objects.filter(recipient=cible, type="role_changed").first()
    assert n is not None
    assert "Collaborateur" in n.message and "Clerc principal" in n.message


@pytest.mark.django_db
def test_une_promotion_en_notaire_impose_le_second_facteur():
    """Le MFA est obligatoire pour un notaire : la révocation de session force
    le passage par LoginView, qui l'exige."""
    _, cible, client = _notaire_et_cible()
    client.patch("/api/users", {"id": cible.pk, "role": "admin", "motif": "Association."},
                 format="json")
    cible.refresh_from_db()
    assert cible.role == "admin"

    r = Client().post("/api/auth/login", {"email": "c6@x.ci", "password": "Mdp!123456"},
                      content_type="application/json")
    assert r.status_code == 202
    assert r.json()["mfaRequired"] is True


@pytest.mark.django_db
def test_un_role_identique_ne_declenche_rien():
    """Demander le rôle déjà en place ne doit ni couper de session ni polluer
    le journal : la requête est simplement sans objet."""
    _, cible, client = _notaire_et_cible()
    version_avant = cible.session_version
    r = client.patch("/api/users",
                     {"id": cible.pk, "role": "collaborateur", "motif": "Sans changement."},
                     format="json")
    assert r.status_code == 400
    assert "Aucune modification" in r.data["detail"]
    cible.refresh_from_db()
    assert cible.role == "collaborateur"
    assert cible.session_version == version_avant, "aucune session ne doit être coupée"
    assert not AuditLog.objects.filter(action="user_role_changed", target_id=str(cible.pk)).exists()
