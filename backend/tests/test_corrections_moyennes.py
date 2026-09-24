"""Corrections 🟡 de l'audit pré-production — non-régression."""
import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from accounts.models import User
from dossiers.models import Dossier, DossierAssignment


@pytest.fixture(autouse=True)
def _cache_propre():
    cache.clear()
    yield
    cache.clear()


def _c(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def _etude():
    n = User.objects.create_user(email="n@e.ci", password="Mdp!Notaire-2026", role="admin")
    cl = User.objects.create_user(email="cl@e.ci", password="Mdp!Clerc-2026x", role="clerc")
    co = User.objects.create_user(email="co@e.ci", password="Mdp!Collab-2026x", role="collaborateur")
    restreint = Dossier.objects.create(reference="SUC-2026-00001", domaine="SUC", nom="Divorce Yao", client="B",
                                       niveau_de_confidentialite="Restreint", created_by=n)
    return n, cl, co, restreint


# ---------------------------------------------------------------------------
# 1–2. Tâches et demandes d'accès : ni écriture croisée, ni sondage
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_une_tache_ne_se_rattache_qu_a_un_dossier_accessible_aux_deux():
    n, cl, co, restreint = _etude()
    inexistant = _c(cl).post("/api/tasks", {"title": "t", "dossier": "SUC-2026-09999"}, format="json")
    interdit = _c(cl).post("/api/tasks", {"title": "t", "dossier": restreint.reference}, format="json")
    assert inexistant.status_code == interdit.status_code == 400
    assert inexistant.data == interdit.data, "inexistant et interdit doivent être indiscernables"
    DossierAssignment.objects.create(dossier=restreint, user=cl, role="clerc_responsable", assigned_by=n)
    vers_non_affecte = _c(cl).post("/api/tasks", {"title": "t", "dossier": restreint.reference, "assignedTo": co.pk}, format="json")
    assert vers_non_affecte.status_code == 400 and "assignedTo" in vers_non_affecte.data
    assert _c(cl).post("/api/tasks", {"title": "t", "dossier": restreint.reference}, format="json").status_code == 201


@pytest.mark.django_db
def test_une_demande_d_acces_ne_revele_ni_l_existence_ni_l_intitule_d_un_dossier():
    n, cl, co, restreint = _etude()
    existant = _c(co).post("/api/access-requests", {"dossier": restreint.reference, "motif": "x"}, format="json")
    inexistant = _c(co).post("/api/access-requests", {"dossier": "SUC-2026-09999", "motif": "x"}, format="json")
    doublon = _c(co).post("/api/access-requests", {"dossier": restreint.reference, "motif": "x"}, format="json")
    assert (existant.status_code, existant.data) == (inexistant.status_code, inexistant.data) == (doublon.status_code, doublon.data)
    mes_demandes = _c(co).get("/api/access-requests").data
    assert len(mes_demandes) == 1 and "Divorce" not in str(mes_demandes), "l'intitulé reste secret avant autorisation"
    assert "Divorce Yao" in str(_c(n).get("/api/access-requests").data), "le notaire, lui, voit l'intitulé pour décider"


# ---------------------------------------------------------------------------
# Changement de mot de passe et validation du profil
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_changer_son_mot_de_passe_ferme_les_autres_sessions():
    User.objects.create_user(email="co@e.ci", password="Mdp!Collab-2026x", role="collaborateur")
    ancienne = APIClient().post("/api/auth/login", {"email": "co@e.ci", "password": "Mdp!Collab-2026x"}, format="json").data["token"]
    autre_poste, ce_poste = APIClient(), APIClient()
    autre_poste.credentials(HTTP_AUTHORIZATION="Bearer " + ancienne)
    ce_poste.credentials(HTTP_AUTHORIZATION="Bearer " + ancienne)
    assert ce_poste.post("/api/me/password", {"currentPassword": "faux", "newPassword": "Nouveau!Mdp-2026x"}, format="json").status_code == 400
    assert ce_poste.post("/api/me/password", {"currentPassword": "Mdp!Collab-2026x", "newPassword": "azerty"}, format="json").status_code == 400
    ok = ce_poste.post("/api/me/password", {"currentPassword": "Mdp!Collab-2026x", "newPassword": "Nouveau!Mdp-2026x"}, format="json")
    assert ok.status_code == 200 and ok.data["token"] != ancienne
    assert autre_poste.get("/api/me").status_code == 401, "les autres sessions tombent"
    ce_poste.credentials(HTTP_AUTHORIZATION="Bearer " + ok.data["token"])
    assert ce_poste.get("/api/me").status_code == 200, "la session courante continue avec son jeton neuf"
    assert User.objects.get(email="co@e.ci").check_password("Nouveau!Mdp-2026x")


@pytest.mark.django_db
def test_le_profil_refuse_les_valeurs_hors_limites_au_lieu_d_une_erreur_500():
    n, *_ = _etude()
    c = _c(n)
    assert c.patch("/api/me", {"phone": "9" * 40}, format="json").status_code == 400
    assert c.patch("/api/me", {"phone": "<script>"}, format="json").status_code == 400
    assert c.patch("/api/me", {"jobTitle": "x" * 200}, format="json").status_code == 400
    assert c.patch("/api/me", {"phone": "+225 07 01 02 03 04", "jobTitle": "Notaire"}, format="json").status_code == 200
    n.refresh_from_db()
    assert n.phone == "+225 07 01 02 03 04"


# ---------------------------------------------------------------------------
# Formats bureautiques : .docx / .xlsx admis, macros et pièges refusés
# ---------------------------------------------------------------------------

import io
import zipfile

from django.test import override_settings

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CLE = dict(DOCUMENT_ENCRYPTION_KEY="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")


def _zip(fichiers: dict, compression=zipfile.ZIP_DEFLATED) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", compression) as z:
        for nom, contenu in fichiers.items():
            z.writestr(nom, contenu)
    return tampon.getvalue()


def _docx(texte="Projet d'acte de vente du lot 12 à Cocody", extra=None, types='<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'):
    corps = f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>{texte}</w:t></w:r></w:p></w:body></w:document>'
    return _zip({"[Content_Types].xml": types, "word/document.xml": corps, **(extra or {})})


def _xlsx():
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    return _zip({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": f'<workbook xmlns="{s}"/>',
                 "xl/sharedStrings.xml": f'<sst xmlns="{s}"><si><t>Décompte des frais de mutation</t></si></sst>'})


def test_l_identification_des_formats_bureautiques():
    from documents.formats import MIME_DOCX, MIME_XLSX, FormatRefuse, identifier
    assert identifier(_docx()) == MIME_DOCX
    assert identifier(_xlsx()) == MIME_XLSX
    pieges = {
        "macro VBA": _docx(extra={"word/vbaProject.bin": b"\x00" * 64}),
        "type macroEnabled": _docx(types="<Types><Override ContentType='application/vnd.ms-word.document.macroEnabled.main+xml'/></Types>"),
        "ActiveX": _docx(extra={"word/activeX/activeX1.bin": b"x"}),
        "objet OLE embarqué": _docx(extra={"word/embeddings/oleObject1.bin": b"x"}),
        "chemin remontant": _docx(extra={"../../etc/passwd": "x"}),
        "zip quelconque": _zip({"lisezmoi.txt": "x"}),
        "bombe zip": _zip({"[Content_Types].xml": "<Types/>", "word/document.xml": "A" * (40 * 1024 * 1024)}),
        "ancien .doc": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100,
        "zip corrompu": b"PK\x03\x04corrompu",
    }
    for nom, contenu in pieges.items():
        with pytest.raises(FormatRefuse):
            identifier(contenu)
            pytest.fail(f"{nom} accepté")


@pytest.mark.django_db
@override_settings(**CLE)
def test_un_projet_d_acte_word_se_depose_se_recherche_et_se_telecharge():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from documents.formats import MIME_DOCX
    from documents.tasks import traiter_file_ocr
    n, cl, co, restreint = _etude()
    Dossier.objects.create(reference="VEN-2026-00001", domaine="VEN", nom="V", client="C", niveau_de_confidentialite="Standard", created_by=n)
    depot = _c(cl).post("/api/documents/upload", {"fichier": SimpleUploadedFile("projet.docx", _docx()), "type_code": "PROJ",
                                                   "dossier": "VEN-2026-00001"}, format="multipart")
    assert depot.status_code == 201, depot.data
    assert depot.data["content_type"] == MIME_DOCX
    macro = _c(cl).post("/api/documents/upload", {"fichier": SimpleUploadedFile("piege.docm", _docx(extra={"word/vbaProject.bin": b"x"})),
                                                   "type_code": "PROJ", "dossier": "VEN-2026-00001"}, format="multipart")
    assert macro.status_code == 400 and "macros" in str(macro.data)
    traiter_file_ocr()
    trouves = _c(n).get("/api/search", {"q": "lot 12"}).json()
    assert [d["reference"] for d in trouves] == [depot.data["reference"]], "le texte du .docx est indexé"
    telechargement = _c(n).get(f"/api/documents/{depot.data['reference']}")
    assert telechargement["Content-Disposition"].startswith("attachment;"), "un fichier Word n'est jamais servi « inline »"
    assert telechargement["Content-Type"] == MIME_DOCX


@pytest.mark.django_db
@override_settings(**CLE)
def test_le_nom_de_fichier_ne_peut_pas_injecter_d_en_tete():
    import hashlib
    from django.core.files.base import ContentFile
    from documents.crypto import encrypt
    from documents.models import Document
    n, *_ = _etude()
    pdf = b"%PDF-1.4\n%%EOF\n"
    chiffre, kid = encrypt(pdf)
    doc = Document.objects.create(reference="DOC_NOM", type="Acte", nom="x", original_filename='a"; x=1\r\nSet-Cookie: pirate=1.pdf',
                                  content_type="application/pdf", size_bytes=len(pdf), sha256=hashlib.sha256(pdf).hexdigest(),
                                  uploaded_by=n, master_reference="DOC_NOM", encryption_key_id=kid)
    doc.fichier.save("a.pdf", ContentFile(chiffre), save=True)
    entete = _c(n).get("/api/documents/DOC_NOM/export")["Content-Disposition"]
    assert "\r" not in entete and "\n" not in entete and entete.count('"') == 2


# ---------------------------------------------------------------------------
# Modèles de checklist gérés depuis l'écran (admin Django fermé)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_notaire_gere_les_modeles_de_checklist_depuis_l_ecran():
    from dossiers.models import ChecklistTemplate
    n, cl, co, _ = _etude()
    assert _c(cl).post("/api/checklist-templates/proposes").status_code == 403
    charge = _c(n).post("/api/checklist-templates/proposes")
    assert charge.status_code == 201 and charge.data["ajoutes"] > 50
    assert _c(n).post("/api/checklist-templates/proposes").data["ajoutes"] == 0, "rechargement sans doublon"
    modele = ChecklistTemplate.objects.filter(domaine="VEN").first()
    assert _c(n).patch("/api/checklist-templates", {"id": modele.pk, "required": False, "reminderDays": 10}, format="json").status_code == 200
    modele.refresh_from_db()
    assert modele.required is False and modele.reminder_days == 10
    assert _c(cl).patch("/api/checklist-templates", {"id": modele.pk, "active": False}, format="json").status_code == 403
    cree = _c(n).post("/api/checklist-templates", {"domaine": "VEN", "label": "Certificat d'urbanisme", "typeCode": "CER"}, format="json")
    assert cree.status_code == 201
    assert _c(cl).get("/api/checklist-templates?domaine=VEN").status_code == 200, "le clerc consulte les modèles"


@pytest.mark.django_db
def test_le_rto_et_le_rpo_sont_enregistres():
    n, *_ = _etude()
    c = _c(n)
    assert c.put("/api/settings", {"cabinet_name": "Étude Konan", "praPca": {"rtoMinutes": 120, "rpoMinutes": 30}}, format="json").status_code == 200
    assert c.get("/api/settings").data["praPca"]["rtoMinutes"] == 120
