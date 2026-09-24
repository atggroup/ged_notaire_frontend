"""Non-régression des correctifs issus de l'audit complet.

Chaque test rejoue une faiblesse qui a été **constatée en exécution** avant
correction. L'ordre suit celui du rapport d'audit.
"""
import base64
import csv
import hashlib
import io
import json
import zipfile

import pytest
from django.core.files.base import ContentFile
from django.core import signing
from django.db import connection, reset_queries
from django.test import Client, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from audit.models import AuditLog
from documents.crypto import encrypt
from documents.models import Document, DocumentFavorite
from dossiers.models import Dossier, DossierAssignment
from permissions_app.models import Permission

KEY = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
PDF = b"%PDF-1.4\n% TESTAMENT OLOGRAPHE - clause secrete\ntrailer<</Root 1 0 R>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _cache_propre():
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


def _piece(reference, dossier, notaire, niveau="Très confidentiel", texte="TESTAMENT OLOGRAPHE clause secrete", **extra):
    chiffre, key_id = encrypt(PDF)
    doc = Document.objects.create(
        reference=reference, dossier=dossier, type="Acte", nom=f"{reference}.pdf",
        original_filename=f"{reference}.pdf", content_type="application/pdf",
        size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(),
        uploaded_by=notaire, master_reference=reference, encryption_key_id=key_id,
        niveau_de_confidentialite=niveau, quality_passed=True,
        extracted_text=texte,
        statut=extra.pop("statut", Document.Status.VALIDATED), **extra,
    )
    doc.fichier.save(f"{reference}.enc", ContentFile(chiffre), save=True)
    return doc


def _etude():
    """Un notaire, un collaborateur AFFECTÉ au dossier, un clerc sans droit."""
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!123456", role="admin")
    collab = User.objects.create_user(email="col@etude.ci", password="Mdp!123456", role="collaborateur")
    clerc = User.objects.create_user(email="cl@etude.ci", password="Mdp!123456", role="clerc")
    dossier = Dossier.objects.create(
        reference="SUC-2026-00001", domaine="SUC", nom="Succession Kouassi",
        client="Famille Kouassi", niveau_de_confidentialite="Restreint", created_by=notaire,
    )
    DossierAssignment.objects.create(dossier=dossier, user=collab, role="collaborateur", assigned_by=notaire)
    return notaire, collab, clerc, dossier


def _client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ---------------------------------------------------------------------------
# 1. L'export de dossier ne contourne plus la confidentialité documentaire
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_l_export_de_dossier_n_emporte_pas_les_pieces_tres_confidentielles():
    """L'accès au dossier n'emporte pas l'accès à chacune de ses pièces.

    Avant correction, un collaborateur simplement affecté recevait dans le ZIP
    le contenu en clair d'une pièce que la route de détail lui refuse (403) —
    et le bouton « Exporter » est présent dans son propre portail.
    """
    notaire, collab, _clerc, dossier = _etude()
    secrete = _piece("DOC_SECRET", dossier, notaire, "Très confidentiel")
    ouverte = _piece("DOC_OUVERT", dossier, notaire, "Standard")

    assert _client(collab).get(f"/api/documents/{secrete.reference}").status_code == 403

    reponse = _client(collab).get(f"/api/dossiers/{dossier.reference}/export")
    assert reponse.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(reponse.content))
    joints = [n for n in archive.namelist() if n.startswith("Documents/")]

    assert not any(secrete.reference in n for n in joints), "la pièce interdite est dans le ZIP"
    assert any(ouverte.reference in n for n in joints), "la pièce autorisée doit rester exportée"
    for nom in joints:
        assert PDF not in archive.read(nom) or ouverte.reference in nom

    bordereau = archive.read("bordereau.csv").decode("utf-8")
    assert secrete.reference not in bordereau, "le bordereau nomme la pièce interdite"
    assert "1 pièce(s) du dossier ne figurent pas" in bordereau, "l'export doit annoncer l'omission"

    trace = AuditLog.objects.filter(action="dossier_exported").first()
    assert trace.metadata["pieces_omises"] == 1


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_le_notaire_exporte_bien_tout_le_dossier():
    """Le correctif ne doit rien retirer à qui a le droit de tout voir."""
    notaire, _collab, _clerc, dossier = _etude()
    _piece("DOC_A", dossier, notaire, "Très confidentiel")
    _piece("DOC_B", dossier, notaire, "Standard")
    archive = zipfile.ZipFile(io.BytesIO(_client(notaire).get(f"/api/dossiers/{dossier.reference}/export").content))
    assert len([n for n in archive.namelist() if n.startswith("Documents/")]) == 2


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_une_piece_detruite_ne_fait_plus_echouer_l_export():
    """Une pièce détruite n'a plus de binaire : la lire levait une ValueError
    et renvoyait une 500 au lieu de l'export du dossier."""
    notaire, _collab, _clerc, dossier = _etude()
    _piece("DOC_VIVANT", dossier, notaire, "Standard")
    detruite = _piece("DOC_DETRUIT", dossier, notaire, "Standard")
    detruite.fichier.delete(save=False)
    detruite.fichier = ""
    detruite.statut = Document.Status.DESTROYED
    detruite.save()

    reponse = _client(notaire).get(f"/api/dossiers/{dossier.reference}/export")
    assert reponse.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(reponse.content))
    joints = [n for n in archive.namelist() if n.startswith("Documents/")]
    assert len(joints) == 1
    lignes = list(csv.reader(io.StringIO(archive.read("bordereau.csv").decode("utf-8"))))
    ligne_detruite = next(l for l in lignes if l and l[0] == "DOC_DETRUIT")
    assert ligne_detruite[-1] == "non", "le bordereau doit dire que le binaire est absent"


# ---------------------------------------------------------------------------
# 2. La file de numérisation respecte les habilitations
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_la_file_de_numerisation_ne_livre_pas_le_texte_d_une_piece_interdite():
    """Le rôle ouvre la file ; il n'ouvre pas les pièces. La file renvoyait le
    nom ET le texte OCR d'une pièce que la route de détail refuse au clerc.

    Le clerc est ici AFFECTÉ au dossier : il doit donc voir ce qui relève de
    son affectation, et seulement cela — le « Très confidentiel » exige une
    habilitation nominative que l'affectation ne remplace pas.
    """
    notaire, _collab, clerc, dossier = _etude()
    DossierAssignment.objects.create(dossier=dossier, user=clerc, role="clerc_responsable", assigned_by=notaire)
    secrete = _piece("DOC_FILE_SECRET", dossier, notaire, "Très confidentiel", statut=Document.Status.TO_INDEX)
    ouverte = _piece("DOC_FILE_OUVERT", dossier, notaire, "Standard", texte="bordereau de depot", statut=Document.Status.TO_INDEX)

    assert _client(clerc).get(f"/api/documents/{secrete.reference}").status_code == 403

    file_attente = _client(clerc).get("/api/documents/queue")
    assert file_attente.status_code == 200
    references = [d["reference"] for d in file_attente.json()]
    assert secrete.reference not in references
    assert ouverte.reference in references
    assert all("clause secrete" not in (d.get("extracted_text") or "") for d in file_attente.json())

    resume = _client(clerc).get("/api/dashboard/summary").json()
    assert resume["pendingIndexationCount"] == 1, "le compteur doit suivre la liste"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_un_clerc_etranger_au_dossier_ne_voit_rien_dans_la_file():
    notaire, _collab, clerc, dossier = _etude()
    _piece("DOC_ETRANGER", dossier, notaire, "Standard", statut=Document.Status.TO_INDEX)
    assert _client(clerc).get("/api/documents/queue").json() == []


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_le_deposant_garde_sa_propre_numerisation_dans_sa_file():
    """On ne peut pas exiger le contrôle qualité d'une numérisation tout en la
    masquant à celui qui vient de la faire."""
    notaire, _collab, clerc, dossier = _etude()
    DossierAssignment.objects.create(dossier=dossier, user=clerc, role="clerc_responsable", assigned_by=notaire)
    sienne = _piece("DOC_SIENNE", dossier, clerc, "Très confidentiel", statut=Document.Status.TO_INDEX)
    references = [d["reference"] for d in _client(clerc).get("/api/documents/queue").json()]
    assert sienne.reference in references


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_on_ne_depose_pas_dans_un_dossier_auquel_on_n_a_pas_acces():
    """Rien ne vérifiait l'accès au dossier visé : un clerc pouvait glisser une
    pièce dans une affaire confidentielle qu'il n'a pas le droit d'ouvrir."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    notaire, _collab, clerc, dossier = _etude()
    dossier.niveau_de_confidentialite = "Confidentiel"
    dossier.save(update_fields=["niveau_de_confidentialite"])

    fichier = SimpleUploadedFile("acte.pdf", PDF, content_type="application/pdf")
    reponse = _client(clerc).post("/api/documents/upload",
                                  {"fichier": fichier, "type": "Acte", "dossier": dossier.reference},
                                  format="multipart")
    assert reponse.status_code == 400
    assert not Document.objects.filter(dossier=dossier).exists()

    DossierAssignment.objects.create(dossier=dossier, user=clerc, role="clerc_responsable", assigned_by=notaire)
    fichier = SimpleUploadedFile("acte.pdf", PDF, content_type="application/pdf")
    ok = _client(clerc).post("/api/documents/upload",
                             {"fichier": fichier, "type": "Acte", "dossier": dossier.reference},
                             format="multipart")
    assert ok.status_code == 201, ok.data


# ---------------------------------------------------------------------------
# 3. La connexion Google n'échappe plus au second facteur
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_connexion_google_exige_le_second_facteur_du_notaire():
    """Google atteste l'adresse e-mail, pas la possession du second facteur.
    Le chemin Google délivrait une session notaire complète de 8 h sans code."""
    from accounts.views import GOOGLE_SESSION_SALT
    notaire = User.objects.create_user(email="n.google@etude.ci", password="Mdp!123456", role="admin")
    ticket = signing.dumps({"user_id": notaire.pk}, salt=GOOGLE_SESSION_SALT)

    reponse = Client().post("/api/auth/oauth/google/consume", {"ticket": ticket},
                            content_type="application/json")
    assert reponse.status_code == 202
    assert reponse.json()["mfaRequired"] is True
    assert "token" not in reponse.json(), "aucun jeton ne doit sortir avant le second facteur"
    notaire.refresh_from_db()
    assert notaire.session_mfa_verified is False


@pytest.mark.django_db
def test_la_connexion_google_reste_directe_pour_un_compte_sans_exigence_mfa():
    """Le correctif ne doit pas imposer un code à qui la politique n'en demande pas."""
    from accounts.views import GOOGLE_SESSION_SALT
    collab = User.objects.create_user(email="col.google@etude.ci", password="Mdp!123456", role="collaborateur")
    ticket = signing.dumps({"user_id": collab.pk}, salt=GOOGLE_SESSION_SALT)
    reponse = Client().post("/api/auth/oauth/google/consume", {"ticket": ticket},
                            content_type="application/json")
    assert reponse.status_code == 200
    assert reponse.json()["token"]


# ---------------------------------------------------------------------------
# 4. La politique de mot de passe s'applique réellement
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("mot_de_passe", ["Password1", "Azerty12", "Abcd1234", "Motdepasse1"])
def test_les_mots_de_passe_trop_courants_ou_trop_courts_sont_refuses(mot_de_passe):
    """`AUTH_PASSWORD_VALIDATORS` n'était jamais appelé : la liste des mots de
    passe les plus répandus était de la configuration morte."""
    from accounts.serializers import PasswordSerializer
    assert not PasswordSerializer(data={"password": mot_de_passe}).is_valid()


@pytest.mark.django_db
def test_un_mot_de_passe_conforme_reste_accepte():
    from accounts.serializers import PasswordSerializer
    assert PasswordSerializer(data={"password": "Minute-Kouassi-2026"}).is_valid()


# ---------------------------------------------------------------------------
# 5. Le niveau du dossier est un plancher, jamais une déclassification
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_abaisser_un_dossier_ne_declassifie_pas_ses_pieces():
    """Rouvrir un dossier remettait à « Standard » une pièce que le notaire
    avait lui-même classée « Très confidentiel » — en silence."""
    notaire, collab, _clerc, dossier = _etude()
    secrete = _piece("DOC_PLANCHER", dossier, notaire, "Très confidentiel")

    reponse = _client(notaire).patch(f"/api/dossiers/{dossier.reference}",
                                     {"niveau_de_confidentialite": "Standard"}, format="json")
    assert reponse.status_code == 200
    assert reponse.json()["piecesAbaissees"] == 0
    secrete.refresh_from_db()
    assert secrete.niveau_de_confidentialite == "Très confidentiel"
    assert _client(collab).get(f"/api/documents/{secrete.reference}").status_code == 403


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_relever_un_dossier_releve_bien_les_pieces_restees_en_dessous():
    notaire, _collab, _clerc, dossier = _etude()
    ouverte = _piece("DOC_RELEVE", dossier, notaire, "Standard")
    reponse = _client(notaire).patch(f"/api/dossiers/{dossier.reference}",
                                     {"niveau_de_confidentialite": "Confidentiel"}, format="json")
    assert reponse.json()["piecesRelevees"] == 1
    ouverte.refresh_from_db()
    assert ouverte.niveau_de_confidentialite == "Confidentiel"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_l_abaissement_des_pieces_reste_possible_mais_explicite_et_trace():
    notaire, _collab, _clerc, dossier = _etude()
    secrete = _piece("DOC_EXPLICITE", dossier, notaire, "Très confidentiel")
    reponse = _client(notaire).patch(
        f"/api/dossiers/{dossier.reference}",
        {"niveau_de_confidentialite": "Standard", "appliquerAuxPieces": True}, format="json")
    assert reponse.json()["piecesAbaissees"] == 1
    secrete.refresh_from_db()
    assert secrete.niveau_de_confidentialite == "Standard"
    trace = AuditLog.objects.filter(action="dossier_confidentiality_updated").first()
    assert trace.metadata["propagation"]["pieces_abaissees"] == 1


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_un_refus_ne_laisse_aucune_piece_modifiee():
    """Les pièces n'étaient écrites qu'après toutes les validations : un gel
    refusé plus bas dans la vue ne doit rien laisser derrière lui."""
    notaire, _collab, _clerc, dossier = _etude()
    ouverte = _piece("DOC_ATOMIQUE", dossier, notaire, "Standard")
    reponse = _client(notaire).patch(
        f"/api/dossiers/{dossier.reference}",
        {"niveau_de_confidentialite": "Confidentiel", "legalHold": True}, format="json")
    assert reponse.status_code == 400  # motif de gel manquant
    ouverte.refresh_from_db()
    assert ouverte.niveau_de_confidentialite == "Standard"
    dossier.refresh_from_db()
    assert dossier.niveau_de_confidentialite == "Restreint"


# ---------------------------------------------------------------------------
# 6. La révocation d'habilitation est toujours ciblée
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_revoquer_sans_critere_n_efface_plus_toutes_les_habilitations():
    """Un DELETE au corps vide partait de `Permission.objects.all()`."""
    notaire, collab, clerc, dossier = _etude()
    piece = _piece("DOC_HAB", dossier, notaire, "Confidentiel")
    Permission.objects.create(user=collab, document=piece, granted_by=notaire)
    Permission.objects.create(user=clerc, document=piece, granted_by=notaire)

    reponse = _client(notaire).delete("/api/permissions", {}, format="json")
    assert reponse.status_code == 400
    assert Permission.objects.count() == 2, "aucune habilitation ne doit disparaître"


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_une_revocation_ciblee_est_tracee_avec_son_detail():
    notaire, collab, clerc, dossier = _etude()
    piece = _piece("DOC_HAB2", dossier, notaire, "Confidentiel")
    cible = Permission.objects.create(user=collab, document=piece, granted_by=notaire)
    Permission.objects.create(user=clerc, document=piece, granted_by=notaire)

    reponse = _client(notaire).delete("/api/permissions", {"id": cible.pk}, format="json")
    assert reponse.status_code == 200
    assert Permission.objects.count() == 1
    trace = AuditLog.objects.filter(action="permission_revoked").first()
    assert trace.metadata["nombre"] == 1
    assert trace.metadata["revoquees"][0]["beneficiaire"] == collab.email


# ---------------------------------------------------------------------------
# 7. L'étude garde toujours un notaire actif
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_dernier_notaire_ne_peut_pas_etre_suspendu():
    """Sans notaire actif, plus personne ne valide un acte, ne gère les comptes
    ni n'accède aux clés : l'étude est bloquée."""
    notaire = User.objects.create_user(email="seul@etude.ci", password="Mdp!123456", role="admin")
    autre = User.objects.create_user(email="autre@etude.ci", password="Mdp!123456", role="admin")
    client = _client(notaire)

    assert client.patch("/api/users", {"id": autre.pk, "isActive": False, "reason": "Départ."},
                        format="json").status_code == 200

    reponse = client.patch("/api/users", {"id": notaire.pk, "isActive": False, "reason": "x"}, format="json")
    assert reponse.status_code == 409
    notaire.refresh_from_db()
    assert notaire.is_active is True
    assert User.objects.filter(role="admin", is_active=True).count() == 1


@pytest.mark.django_db
def test_un_notaire_ne_suspend_pas_son_propre_acces():
    notaire = User.objects.create_user(email="n.self@etude.ci", password="Mdp!123456", role="admin")
    User.objects.create_user(email="n.bis@etude.ci", password="Mdp!123456", role="admin")
    reponse = _client(notaire).patch("/api/users", {"id": notaire.pk, "isActive": False, "reason": "x"}, format="json")
    assert reponse.status_code == 409
    assert "votre propre accès" in reponse.data["detail"]


# ---------------------------------------------------------------------------
# 8. La recherche ne remonte plus les versions périmées
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_la_recherche_ignore_les_versions_remplacees():
    """Un clerc pouvait travailler de bonne foi sur une pièce périmée."""
    notaire, _collab, _clerc, dossier = _etude()
    v1 = _piece("DOC_V1", dossier, notaire, "Standard")
    v1.is_current = False
    v1.save(update_fields=["is_current"])
    v2 = _piece("DOC_V2", dossier, notaire, "Standard")
    v2.master_reference, v2.version, v2.previous_version = v1.reference, 2, v1
    v2.save()

    trouves = [d["reference"] for d in _client(notaire).get("/api/search?q=DOC_V").json()]
    assert "DOC_V2" in trouves
    assert "DOC_V1" not in trouves

    # L'historique complet reste accessible par la route dédiée.
    versions = [d["reference"] for d in _client(notaire).get(f"/api/documents/{v2.reference}/versions").json()]
    assert set(versions) == {"DOC_V1", "DOC_V2"}


# ---------------------------------------------------------------------------
# 9. Le favori ne confirme plus l'existence d'une pièce interdite
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_mettre_en_favori_une_piece_interdite_est_refuse():
    notaire, collab, _clerc, dossier = _etude()
    secrete = _piece("DOC_FAV", dossier, notaire, "Très confidentiel")
    reponse = _client(collab).post("/api/documents/favorite", {"ref": secrete.reference}, format="json")
    assert reponse.status_code == 404, "même réponse qu'une référence inexistante"
    assert not DocumentFavorite.objects.filter(user=collab, document=secrete).exists()


# ---------------------------------------------------------------------------
# 10. Le journal d'audit est exploitable
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_journal_restitue_l_ancienne_et_la_nouvelle_valeur():
    """Le cahier des charges les exige ; elles étaient écrites en base et ne
    sortaient d'aucune API — le journal était consultable, pas exploitable."""
    notaire = User.objects.create_user(email="n.audit@etude.ci", password="Mdp!123456", role="admin")
    cible = User.objects.create_user(email="c.audit@etude.ci", password="Mdp!123456", role="collaborateur")
    client = _client(notaire)
    client.patch("/api/users", {"id": cible.pk, "role": "clerc", "motif": "Promotion."}, format="json")

    journal = client.get("/api/audit?scope=cabinet&action=user_role_changed").json()
    assert journal["total"] >= 1
    entree = journal["entries"][0]
    assert entree["details"]["previous"] == "collaborateur"
    assert entree["details"]["next"] == "clerc"
    assert entree["details"]["motif"] == "Promotion."
    assert entree["entryHash"]

    export = client.get("/api/audit/export?scope=cabinet")
    lignes = export.content.decode("utf-8").splitlines()
    assert "details" in lignes[0]
    assert any("\"previous\": \"collaborateur\"" in l or "previous" in l and "collaborateur" in l for l in lignes[1:])


@pytest.mark.django_db
def test_le_journal_se_filtre_par_periode_et_par_acteur():
    notaire = User.objects.create_user(email="n.filtre@etude.ci", password="Mdp!123456", role="admin")
    client = _client(notaire)
    client.get("/api/dashboard/summary")
    total = client.get("/api/audit?scope=cabinet").json()["total"]
    aucun = client.get("/api/audit?scope=cabinet&acteur=inexistant@nulle.part").json()
    assert aucun["total"] == 0 and total >= 0
    hors_periode = client.get("/api/audit?scope=cabinet&date_to=2000-01-01").json()
    assert hors_periode["total"] == 0


@pytest.mark.django_db
def test_le_journal_du_cabinet_reste_reserve_au_notaire():
    clerc = User.objects.create_user(email="cl.audit@etude.ci", password="Mdp!123456", role="clerc")
    assert _client(clerc).get("/api/audit?scope=cabinet").status_code == 403
    assert _client(clerc).get("/api/audit/export?scope=cabinet").status_code == 403


# ---------------------------------------------------------------------------
# 11. Les listes ne repartent plus en N+1
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="", DEBUG=True)
def test_le_cout_d_une_liste_ne_depend_pas_du_nombre_de_documents():
    """82 requêtes pour 80 documents avant correction : `is_favorite` en
    déclenchait une par pièce, `has_access` jusqu'à trois."""
    notaire, _collab, _clerc, dossier = _etude()
    client = _client(notaire)

    for i in range(5):
        _piece(f"DOC_N{i:03d}", dossier, notaire, "Standard")
    client.get("/api/documents")  # amorçage (schéma, session)
    reset_queries()
    client.get("/api/documents")
    petit = len(connection.queries)

    for i in range(5, 40):
        _piece(f"DOC_N{i:03d}", dossier, notaire, "Standard")
    reset_queries()
    reponse = client.get("/api/documents")
    grand = len(connection.queries)

    assert len(reponse.json()) == 40
    assert grand <= petit + 2, (
        f"le coût suit encore la volumétrie : {petit} requêtes pour 5 pièces, "
        f"{grand} pour 40"
    )


# ---------------------------------------------------------------------------
# 12. La limitation de débit ne se contourne plus par un en-tête
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_limitation_de_debit_ignore_un_x_forwarded_for_falsifie():
    """nginx concatène l'en-tête envoyé par le client : il suffisait de le
    faire varier pour repartir à zéro (mesuré : 26 tentatives, aucun 429).
    L'adresse vient désormais de `X-Real-IP`, que `proxy_set_header` REMPLACE.
    """
    anonyme = Client()
    codes = []
    for i in range(30):
        reponse = anonyme.post(
            "/api/auth/login", {"email": f"inconnu{i}@x.ci", "password": "x"},
            content_type="application/json",
            HTTP_X_FORWARDED_FOR=f"10.0.0.{i}",          # falsifié par le client
            HTTP_X_REAL_IP="203.0.113.9",                 # posé par nginx
        )
        codes.append(reponse.status_code)
    assert 429 in codes, "la limitation de débit reste contournable"


@pytest.mark.django_db
def test_l_adresse_du_journal_d_audit_n_est_pas_dictee_par_l_interesse():
    """L'adresse consignée venait de X-Forwarded-For : la personne tracée
    choisissait l'adresse inscrite à son propre dossier."""
    notaire = User.objects.create_user(email="n.ip@etude.ci", password="Mdp!123456", role="admin")
    client = APIClient()
    client.force_authenticate(user=notaire)
    client.credentials(HTTP_X_FORWARDED_FOR="1.2.3.4", HTTP_X_REAL_IP="203.0.113.9")
    client.get("/api/dashboard/summary")
    trace = AuditLog.objects.order_by("-id").first()
    if trace is not None and trace.ip_address:
        assert trace.ip_address != "1.2.3.4", "l'adresse falsifiée a été consignée"
        assert trace.ip_address == "203.0.113.9"


@pytest.mark.django_db
def test_une_adresse_invalide_ne_pollue_pas_le_journal():
    from ged_backend.reseau import adresse_client

    class FausseRequete:
        META = {"HTTP_X_REAL_IP": "pas-une-adresse", "REMOTE_ADDR": "10.1.2.3"}

    assert adresse_client(FausseRequete()) == "10.1.2.3"

    class SansRien:
        META = {}

    assert adresse_client(SansRien()) is None


# ---------------------------------------------------------------------------
# 13. Les services annexes exécutent bien leur commande
# ---------------------------------------------------------------------------

def test_l_entrypoint_execute_la_commande_qu_on_lui_donne():
    """`entrypoint.sh` ignorait ses arguments et lançait gunicorn quoi qu'il
    arrive : le conteneur « backup » faisait donc tourner un second serveur web
    au lieu de la boucle de sauvegarde. La sauvegarde automatique de l'étude
    n'avait jamais lieu, alors que l'écran de supervision laissait croire le
    contraire."""
    import shutil
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "deploy" / "entrypoint.sh"
    sh = shutil.which("sh") or shutil.which("bash")
    if not sh:
        pytest.skip("aucun interpréteur POSIX disponible sur cette machine")

    resultat = subprocess.run(
        [sh, str(script), "echo", "commande-du-sidecar"],
        capture_output=True, text=True, timeout=60,
        env={"RUN_MIGRATIONS": "false", "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert resultat.returncode == 0, resultat.stderr
    assert "commande-du-sidecar" in resultat.stdout
    assert "gunicorn" not in resultat.stdout


def test_les_sidecars_ne_migrent_pas_et_portent_bien_leur_commande():
    """Trois conteneurs qui lancent `migrate` au même instant, ce sont trois
    transactions concurrentes sur la table des migrations."""
    from pathlib import Path
    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count('RUN_MIGRATIONS: "false"') == 2, "sauvegarde et rappels doivent être exclus des migrations"
    # Les deux exécutants passent par `run_worker` (travaux verrouillés,
    # tracés, retentés) : plus aucune boucle shell qui avale les erreurs.
    assert '"run_worker", "--groupe", "sauvegarde"' in compose
    assert '"run_worker", "--sauf-groupe", "sauvegarde"' in compose
    assert "|| true" not in compose, "une erreur de travail ne doit plus jamais être avalée"


# ---------------------------------------------------------------------------
# 14. Les rappels d'échéance sont réellement émis
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_rappel_d_echeance_est_envoye_une_fois_et_une_seule():
    """Le champ « Rappel » était collecté, stocké, renvoyé par l'API — et
    jamais envoyé. C'était la seule fonctionnalité simulée de l'application."""
    from datetime import timedelta
    from django.core.management import call_command
    from django.utils import timezone
    from notifications.models import Notification, Task

    notaire = User.objects.create_user(email="n.rappel@etude.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c.rappel@etude.ci", password="Mdp!123456", role="clerc")
    maintenant = timezone.now()

    due = Task.objects.create(
        title="Obtenir le certificat fiscal", assigned_to=clerc, assigned_by=notaire,
        due_at=maintenant + timedelta(hours=2), reminder_at=maintenant - timedelta(minutes=5),
    )
    plus_tard = Task.objects.create(
        title="Relancer le cadastre", assigned_to=clerc, assigned_by=notaire,
        due_at=maintenant + timedelta(days=3), reminder_at=maintenant + timedelta(days=2),
    )

    call_command("envoyer_rappels")
    rappels = Notification.objects.filter(recipient=clerc, type="task_reminder")
    assert rappels.count() == 1
    assert "certificat fiscal" in rappels.first().message
    due.refresh_from_db()
    assert due.reminder_sent_at is not None

    call_command("envoyer_rappels")
    assert Notification.objects.filter(recipient=clerc, type="task_reminder").count() == 1, \
        "un second passage ne doit pas renvoyer le même rappel"

    plus_tard.refresh_from_db()
    assert plus_tard.reminder_sent_at is None, "un rappel futur ne part pas d'avance"


@pytest.mark.django_db
def test_une_echeance_depassee_alerte_l_interesse_et_le_donneur_d_ordre():
    from datetime import timedelta
    from django.core.management import call_command
    from django.utils import timezone
    from notifications.models import Notification, Task

    notaire = User.objects.create_user(email="n.retard@etude.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="c.retard@etude.ci", password="Mdp!123456", role="clerc")
    Task.objects.create(title="Déposer l'acte", assigned_to=clerc, assigned_by=notaire,
                        due_at=timezone.now() - timedelta(days=1))

    call_command("envoyer_rappels")
    assert Notification.objects.filter(recipient=clerc, type="task_overdue").count() == 1
    assert Notification.objects.filter(recipient=notaire, type="task_overdue").count() == 1
    call_command("envoyer_rappels")
    assert Notification.objects.filter(type="task_overdue").count() == 2, "pas de rappel en boucle"


@pytest.mark.django_db
def test_une_tache_terminee_ne_declenche_plus_rien():
    from datetime import timedelta
    from django.core.management import call_command
    from django.utils import timezone
    from notifications.models import Notification, Task

    notaire = User.objects.create_user(email="n.fini@etude.ci", password="Mdp!123456", role="admin")
    Task.objects.create(title="Déjà faite", assigned_to=notaire, assigned_by=notaire,
                        due_at=timezone.now() - timedelta(days=2),
                        reminder_at=timezone.now() - timedelta(days=3),
                        status=Task.Status.DONE)
    call_command("envoyer_rappels")
    assert not Notification.objects.filter(type__startswith="task_").exists()


# ---------------------------------------------------------------------------
# 15. L'OCR ne bloque plus la requête de dépôt
# ---------------------------------------------------------------------------

@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_le_depot_ne_lance_plus_l_ocr_dans_la_requete():
    """Rasteriser 50 pages à 300 dpi occupait un fil d'exécution plusieurs
    minutes : gunicorn n'en offre que six et nginx coupe à 180 s."""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import call_command

    notaire, _collab, _clerc, dossier = _etude()
    fichier = SimpleUploadedFile("acte.pdf", PDF, content_type="application/pdf")
    reponse = _client(notaire).post("/api/documents/upload",
                                    {"fichier": fichier, "type": "Acte", "dossier": dossier.reference},
                                    format="multipart")
    assert reponse.status_code == 201
    depose = Document.objects.get(reference=reponse.data["reference"])
    assert depose.ocr_status == Document.OCRStatus.PENDING
    assert depose.ocr_processed_at is None

    call_command("traiter_ocr", lot=5)
    depose.refresh_from_db()
    assert depose.ocr_status != Document.OCRStatus.PENDING
    assert depose.ocr_processed_at is not None


@pytest.mark.django_db
@override_settings(DOCUMENT_ENCRYPTION_KEY=KEY, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
def test_le_traitement_ocr_ignore_les_pieces_detruites():
    from django.core.management import call_command
    notaire, _collab, _clerc, dossier = _etude()
    detruite = _piece("DOC_OCR_DETRUIT", dossier, notaire, "Standard")
    detruite.fichier.delete(save=False)
    detruite.fichier = ""
    detruite.statut = Document.Status.DESTROYED
    detruite.ocr_status = Document.OCRStatus.PENDING
    detruite.save()
    call_command("traiter_ocr")  # ne doit pas lever
    detruite.refresh_from_db()
    assert detruite.ocr_status == Document.OCRStatus.PENDING
