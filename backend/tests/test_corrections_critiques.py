"""Corrections 🟠 de l'audit pré-production — non-régression."""
import pytest
from django.core import mail
from django.core.cache import cache
from django.test import Client, override_settings
from rest_framework.test import APIClient

from accounts.models import OTPCode, User


@pytest.fixture(autouse=True)
def _cache_propre():
    cache.clear()
    yield
    cache.clear()


def _code_otp_envoye() -> str:
    """Le code en clair n'existe que dans l'e-mail envoyé."""
    import re
    return re.search(r"(\d{6})", mail.outbox[-1].body).group(1)


def _jeton_de_reinitialisation(email: str) -> str:
    anonyme = APIClient()
    assert anonyme.post("/api/auth/forgot-password/send-code", {"email": email}, format="json").status_code == 200
    reponse = anonyme.post("/api/auth/forgot-password/verify-code", {"email": email, "code": _code_otp_envoye()}, format="json")
    assert reponse.status_code == 200, reponse.data
    return reponse.data["resetToken"]


# ---------------------------------------------------------------------------
# 1. La réinitialisation ferme les sessions et le lien est à usage unique
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_la_reinitialisation_ferme_toutes_les_sessions_ouvertes():
    User.objects.create_user(email="co@etude.ci", password="Mdp!Initial-2026", role="collaborateur")
    jeton_session = APIClient().post("/api/auth/login", {"email": "co@etude.ci", "password": "Mdp!Initial-2026"}, format="json").data["token"]
    intrus = APIClient()
    intrus.credentials(HTTP_AUTHORIZATION="Bearer " + jeton_session)
    assert intrus.get("/api/me").status_code == 200
    jeton = _jeton_de_reinitialisation("co@etude.ci")
    assert APIClient().post("/api/auth/forgot-password/reset", {"resetToken": jeton, "password": "Nouveau!Mdp-2026x"}, format="json").status_code == 200
    assert intrus.get("/api/me").status_code == 401, "une session ouverte avant la réinitialisation doit tomber"
    user = User.objects.get(email="co@etude.ci")
    from notifications.models import Notification
    assert Notification.objects.filter(recipient=user, type="security", email_status="en_attente").exists()


@pytest.mark.django_db
def test_le_lien_de_reinitialisation_ne_sert_qu_une_fois():
    User.objects.create_user(email="co@etude.ci", password="Mdp!Initial-2026", role="collaborateur")
    jeton = _jeton_de_reinitialisation("co@etude.ci")
    c = APIClient()
    assert c.post("/api/auth/forgot-password/reset", {"resetToken": jeton, "password": "Nouveau!Mdp-2026x"}, format="json").status_code == 200
    reponse = c.post("/api/auth/forgot-password/reset", {"resetToken": jeton, "password": "Pirate!Mdp-2026xy"}, format="json")
    assert reponse.status_code == 400
    assert User.objects.get(email="co@etude.ci").check_password("Nouveau!Mdp-2026x")


# ---------------------------------------------------------------------------
# 2. pdf.js : un PDF piégé ne peut plus faire exécuter de code (CVE-2024-4367)
# ---------------------------------------------------------------------------

def test_chaque_visionneuse_desactive_l_evaluation_de_code_de_pdfjs():
    import re
    from pathlib import Path
    racine = Path(__file__).resolve().parent.parent.parent
    visionneuses = list(racine.glob("*/assets/pdf-viewer.js"))
    assert len(visionneuses) == 3
    for fichier in visionneuses:
        code = fichier.read_text(encoding="utf-8")
        appels = re.findall(r"getDocument\(\{[^}]*\}\)", code)
        assert appels, fichier
        assert all("isEvalSupported: false" in a for a in appels), f"{fichier} : getDocument sans isEvalSupported:false"


# ---------------------------------------------------------------------------
# 3. Admin Django et schéma de l'API fermés au public
# ---------------------------------------------------------------------------

def _resoudre_en_production(chemin: str, **env_sup) -> str:
    from tests.test_bloquants_production import PROD_SAINE, BACKEND
    import os, subprocess, sys
    env = {**os.environ, **PROD_SAINE, **env_sup, "PYTHONIOENCODING": "utf-8"}
    code = f"import django; django.setup(); from django.urls import resolve; print(resolve({chemin!r}).func.__name__)"
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-600:]
    return r.stdout.strip()


def test_l_admin_et_le_schema_sont_absents_en_production_par_defaut():
    assert _resoudre_en_production("/admin/") == "serve_frontend", "l'admin Django ne doit pas être routé"
    assert _resoudre_en_production("/api/schema/") == "serve_frontend", "le schéma de l'API ne doit pas être routé"
    assert _resoudre_en_production("/api/docs/") == "serve_frontend"


def test_le_schema_active_exige_une_connexion():
    from django.conf import settings
    assert _resoudre_en_production("/api/schema/", API_DOCS_ENABLED="true") != "serve_frontend"
    assert settings.SPECTACULAR_SETTINGS["SERVE_PERMISSIONS"] == ["rest_framework.permissions.IsAuthenticated"]


def test_nginx_ne_relaie_plus_l_admin():
    from tests.test_nginx_expose_le_bon_perimetre import resoudre
    corps = resoudre("/admin/login/")
    assert "proxy_pass" not in corps and "return 302 /login.html" in corps


# ---------------------------------------------------------------------------
# 4. Le texte OCR n'est plus diffusé dans les listes
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_texte_ocr_n_apparait_que_sur_la_fiche_de_la_piece(settings, tmp_path):
    import base64, hashlib
    from django.core.files.base import ContentFile
    from documents.crypto import encrypt
    from documents.models import Document
    from dossiers.models import Dossier
    cle = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()
    with override_settings(DOCUMENT_ENCRYPTION_KEY=cle, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID=""):
        notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
        dossier = Dossier.objects.create(reference="SUC-2026-00001", domaine="SUC", nom="S", client="C", created_by=notaire)
        pdf = b"%PDF-1.4\n%%EOF\n"
        chiffre, kid = encrypt(pdf)
        doc = Document.objects.create(reference="DOC_TXT", dossier=dossier, type="Acte", nom="Testament", original_filename="t.pdf",
                                      content_type="application/pdf", size_bytes=len(pdf), sha256=hashlib.sha256(pdf).hexdigest(),
                                      uploaded_by=notaire, master_reference="DOC_TXT", encryption_key_id=kid,
                                      extracted_text="JE LEGUE MA MAISON A MON NEVEU")
        doc.fichier.save("t.pdf", ContentFile(chiffre), save=True)
        c = APIClient(); c.force_authenticate(notaire)
        for url in ("/api/documents", "/api/search?q=MAISON", f"/api/dossiers/{dossier.reference}"):
            corps = c.get(url).content.decode()
            assert "JE LEGUE" not in corps, f"{url} diffuse le texte OCR"
        assert "extracted_text" not in c.get("/api/documents").json()[0]
        assert "JE LEGUE" in c.get("/api/documents/DOC_TXT?format=json").content.decode(), "la fiche garde le texte"


# ---------------------------------------------------------------------------
# 10. Premier compte : politique de mot de passe, pas de superutilisateur Django
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_le_premier_compte_refuse_un_mot_de_passe_faible(monkeypatch):
    import io
    from django.core.management import call_command
    from django.core.management.base import CommandError
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "notaire123")
    with pytest.raises(CommandError, match="refusé"):
        call_command("create_first_admin", "--email", "me.konan@etude.ci", stdout=io.StringIO())
    assert not User.objects.exists()
    monkeypatch.setenv("INITIAL_ADMIN_PASSWORD", "Robuste!Amorcage-2026")
    sortie = io.StringIO()
    call_command("create_first_admin", "--email", "me.konan@etude.ci", stdout=sortie)
    notaire = User.objects.get()
    assert notaire.role == "admin" and not notaire.is_superuser and not notaire.is_staff
    assert "Robuste" not in sortie.getvalue() and "Retirez maintenant INITIAL_ADMIN_PASSWORD" in sortie.getvalue()


@pytest.mark.django_db
def test_le_premier_compte_exige_une_adresse_reelle(monkeypatch):
    import io
    from django.core.management import call_command
    from django.core.management.base import CommandError
    monkeypatch.delenv("INITIAL_ADMIN_EMAIL", raising=False)
    with pytest.raises(CommandError):
        call_command("create_first_admin", stdout=io.StringIO())


# ---------------------------------------------------------------------------
# 6. Antivirus des dépôts (ClamAV)
# ---------------------------------------------------------------------------

import socketserver
import struct
import threading

# Chaîne de test antivirus standard (inoffensive), assemblée pour que ce
# fichier source ne soit pas lui-même signalé par un antivirus.
EICAR = ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()
PDF_SAIN = b"%PDF-1.4\n% acte sain\ntrailer<</Root 1 0 R>>\n%%EOF\n"


class _FauxClamd(socketserver.BaseRequestHandler):
    def handle(self):
        assert self.request.recv(10) == b"zINSTREAM\0"
        recu = b""
        while True:
            longueur = struct.unpack("!L", self._lire(4))[0]
            if not longueur:
                break
            recu += self._lire(longueur)
        self.request.sendall(b"stream: Eicar-Test-Signature FOUND\0" if EICAR in recu else b"stream: OK\0")

    def _lire(self, n):
        data = b""
        while len(data) < n:
            data += self.request.recv(n - len(data))
        return data


@pytest.fixture
def clamd():
    serveur = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _FauxClamd)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    yield serveur.server_address[1]
    serveur.shutdown()
    serveur.server_close()


def _etude_scan():
    import base64
    from dossiers.models import Dossier
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    clerc = User.objects.create_user(email="cl@e.ci", password="Mdp!123456", role="clerc")
    Dossier.objects.create(reference="VEN-2026-00001", domaine="VEN", nom="V", client="C", niveau_de_confidentialite="Standard", created_by=notaire)
    c = APIClient(); c.force_authenticate(clerc)
    return notaire, clerc, c, base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode()


def _deposer(client, contenu, nom="piece.pdf"):
    from django.core.files.uploadedfile import SimpleUploadedFile
    return client.post("/api/documents/upload", {"fichier": SimpleUploadedFile(nom, contenu), "type_code": "ACT", "dossier": "VEN-2026-00001"}, format="multipart")


@pytest.mark.django_db
def test_un_fichier_infecte_est_refuse_trace_et_signale(clamd):
    from audit.models import AuditLog, SecurityAlert
    from documents.models import Document
    notaire, clerc, c, cle = _etude_scan()
    with override_settings(ANTIVIRUS_HOST="127.0.0.1", ANTIVIRUS_PORT=clamd, DOCUMENT_ENCRYPTION_KEY=cle, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID=""):
        # Un PDF qui embarque la signature : le type réel ne suffit pas.
        infecte = _deposer(c, b"%PDF-1.4\n" + EICAR + b"\n%%EOF\n")
        sain = _deposer(c, PDF_SAIN)
    assert infecte.status_code == 400 and "malveillant" in str(infecte.data)
    assert sain.status_code == 201
    assert Document.objects.count() == 1, "le fichier infecté n'est jamais enregistré"
    assert AuditLog.objects.filter(action="document_upload_blocked_virus", user=clerc, result="failure").exists()
    alerte = SecurityAlert.objects.get(rule="fichier_infecte")
    assert alerte.subject_user == clerc and alerte.severity == "haute"


@pytest.mark.django_db
def test_une_nouvelle_version_est_aussi_analysee(clamd):
    from django.core.files.uploadedfile import SimpleUploadedFile
    notaire, clerc, c, cle = _etude_scan()
    with override_settings(ANTIVIRUS_HOST="127.0.0.1", ANTIVIRUS_PORT=clamd, DOCUMENT_ENCRYPTION_KEY=cle, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID=""):
        ref = _deposer(c, PDF_SAIN).data["reference"]
        reponse = c.post(f"/api/documents/{ref}/versions", {"fichier": SimpleUploadedFile("v2.pdf", b"%PDF-1.4\n" + EICAR)}, format="multipart")
    assert reponse.status_code == 400


@pytest.mark.django_db
def test_sans_moteur_antivirus_le_depot_est_suspendu_jamais_accepte():
    from documents.models import Document
    from notifications.models import Notification
    notaire, clerc, c, cle = _etude_scan()
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port_ferme = s.getsockname()[1]; s.close()
    commun = dict(ANTIVIRUS_HOST="127.0.0.1", ANTIVIRUS_PORT=port_ferme, ANTIVIRUS_TIMEOUT=2,
                  DOCUMENT_ENCRYPTION_KEY=cle, DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")
    with override_settings(ANTIVIRUS_REQUIRED=True, **commun):
        assert _deposer(c, PDF_SAIN).status_code == 503
    assert not Document.objects.exists()
    assert Notification.objects.filter(recipient=notaire, type="antivirus_down", severity="critique").exists()
    with override_settings(ANTIVIRUS_REQUIRED=False, **commun):
        assert _deposer(c, PDF_SAIN).status_code == 201, "dérogation explicite : dépôt accepté mais tracé"


def test_la_production_exige_un_antivirus():
    from tests.test_bloquants_production import _demarrer_prod
    resultat = _demarrer_prod(ANTIVIRUS_HOST="")
    assert resultat.returncode != 0 and "ANTIVIRUS_HOST" in resultat.stderr
    assert _demarrer_prod(ANTIVIRUS_HOST="", ANTIVIRUS_DESACTIVE="true").returncode == 0


# ---------------------------------------------------------------------------
# 9. Signal de vie vers une surveillance externe
# ---------------------------------------------------------------------------

@pytest.fixture
def surveillance_externe():
    import http.server
    recus = []

    class Gestion(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            longueur = int(self.headers.get("Content-Length", 0))
            recus.append((self.path, self.rfile.read(longueur).decode("utf-8")))
            self.send_response(200); self.end_headers()

        def log_message(self, *args):
            pass

    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gestion)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    from core import surveillance
    surveillance._dernier_signal["instant"] = None
    yield f"http://127.0.0.1:{serveur.server_address[1]}/ping/abc", recus
    serveur.shutdown(); serveur.server_close()


@pytest.mark.django_db
def test_le_signal_de_vie_distingue_sain_et_en_echec(surveillance_externe):
    from django.utils import timezone
    from core import surveillance
    from core.models import JobRun
    url, recus = surveillance_externe
    with override_settings(MONITORING_PING_URL=url, MONITORING_PING_INTERVAL=300):
        assert surveillance.signaler() is True
        assert surveillance.signaler() is None, "l'intervalle entre deux signaux est respecté"
        JobRun.objects.create(name="sauvegarde", status=JobRun.Status.FAILURE, started_at=timezone.now(), finished_at=timezone.now())
        assert surveillance.signaler(force=True) is False
    assert recus[0][0] == "/ping/abc" and recus[1][0] == "/ping/abc/fail"
    assert "sauvegarde" in recus[1][1]


@pytest.mark.django_db
def test_sans_adresse_de_surveillance_rien_n_est_envoye():
    from core import surveillance
    with override_settings(MONITORING_PING_URL=""):
        assert surveillance.signaler(force=True) is None
    with override_settings(MONITORING_PING_URL="file:///etc/passwd"):
        assert surveillance.signaler(force=True) is None, "seules les adresses HTTP(S) sont appelées"


@pytest.mark.django_db
def test_une_surveillance_injoignable_n_arrete_pas_l_executant():
    from core import surveillance
    surveillance._dernier_signal["instant"] = None
    with override_settings(MONITORING_PING_URL="http://127.0.0.1:9/ping"):
        assert surveillance.signaler(force=True) is False


# ---------------------------------------------------------------------------
# 7. Performance : coût indépendant du volume documentaire
# ---------------------------------------------------------------------------

def _volume(notaire, collab, nb_dossiers, pieces_par_dossier):
    from documents.models import Document
    from dossiers.models import Dossier, DossierAssignment
    for i in range(nb_dossiers):
        d = Dossier.objects.create(reference=f"VEN-2026-{Dossier.objects.count() + 1:05d}", domaine="VEN", nom=f"D{i}", client="C",
                                   niveau_de_confidentialite="Restreint", created_by=notaire)
        DossierAssignment.objects.create(dossier=d, user=collab, role="collaborateur", assigned_by=notaire)
        Document.objects.bulk_create([Document(
            reference=f"DOC_{d.pk}_{j}", dossier=d, type="Acte", nom="p", original_filename="p.pdf", content_type="application/pdf",
            size_bytes=1, sha256="0" * 64, uploaded_by=notaire, master_reference=f"DOC_{d.pk}_{j}", fichier="x.enc",
            extracted_text="TEXTE " * 2000) for j in range(pieces_par_dossier)])


def _nb_requetes(client, url):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    with CaptureQueriesContext(connection) as ctx:
        assert client.get(url).status_code == 200
    return len(ctx.captured_queries)


@pytest.mark.django_db
@pytest.mark.parametrize("role", ["admin", "collaborateur"])
def test_le_tableau_de_bord_et_les_dossiers_ne_grossissent_pas_avec_le_volume(role):
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    collab = User.objects.create_user(email="co@e.ci", password="Mdp!123456", role="collaborateur")
    lecteur = notaire if role == "admin" else collab
    c = APIClient(); c.force_authenticate(lecteur)
    _volume(notaire, collab, 3, 3)
    petit = {u: _nb_requetes(c, u) for u in ("/api/dashboard/summary", "/api/dossiers")}
    _volume(notaire, collab, 12, 25)
    grand = {u: _nb_requetes(c, u) for u in ("/api/dashboard/summary", "/api/dossiers")}
    for url in petit:
        assert grand[url] <= petit[url] + 2, f"{url} : {petit[url]} requêtes pour 9 pièces, {grand[url]} pour 309"


@pytest.mark.django_db
def test_la_liste_des_dossiers_compte_juste_et_se_pagine():
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    collab = User.objects.create_user(email="co@e.ci", password="Mdp!123456", role="collaborateur")
    _volume(notaire, collab, 4, 3)
    c = APIClient(); c.force_authenticate(collab)
    reponse = c.get("/api/dossiers")
    assert reponse["X-Total-Count"] == "4"
    assert [d["documentsCount"] for d in reponse.json()] == [3, 3, 3, 3], "une affectation ne doit pas gonfler le compte"
    page = c.get("/api/dossiers?limit=2&offset=1").json()
    assert len(page) == 2


# ---------------------------------------------------------------------------
# 5. Second facteur par application d'authentification (TOTP)
# ---------------------------------------------------------------------------

CLE_TOTP = dict(DOCUMENT_ENCRYPTION_KEY="MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=", DOCUMENT_ENCRYPTION_KEYS="", DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="")


def test_l_algorithme_respecte_les_vecteurs_officiels_rfc6238():
    import base64
    from accounts import totp
    secret = base64.b32encode(b"12345678901234567890").decode()
    for instant, attendu in ((59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"), (2000000000, "69279037")):
        assert totp.code_pour(secret, instant // 30, chiffres=8) == attendu


def _activer_totp(client, mot_de_passe):
    import time
    from accounts import totp
    assert client.post("/api/auth/totp/setup", {"password": "faux"}, format="json").status_code == 400
    reponse = client.post("/api/auth/totp/setup", {"password": mot_de_passe}, format="json")
    secret = reponse.data["secret"].replace(" ", "")
    assert reponse.data["otpauthUri"].startswith("otpauth://totp/") and secret in reponse.data["otpauthUri"]
    assert client.post("/api/auth/totp/confirm", {"code": "000000"}, format="json").status_code == 400
    code = totp.code_pour(secret, int(time.time() // 30))
    confirme = client.post("/api/auth/totp/confirm", {"code": code}, format="json")
    assert confirme.status_code == 200
    return secret, confirme.data["recoveryCodes"]


def _code_suivant(secret, decalage=1):
    import time
    from accounts import totp
    return totp.code_pour(secret, int(time.time() // 30) + decalage)


@pytest.mark.django_db
@override_settings(**CLE_TOTP)
def test_une_messagerie_compromise_ne_suffit_plus_a_prendre_le_compte_du_notaire():
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!Notaire-2026", role="admin")
    c = APIClient(); c.force_authenticate(notaire)
    secret, codes = _activer_totp(c, "Mdp!Notaire-2026")
    assert len(codes) == 10
    notaire.refresh_from_db()
    assert secret not in notaire.totp_secret, "le secret est stocké chiffré"
    # L'attaquant contrôle la boîte mail : il réinitialise le mot de passe…
    mail.outbox.clear()
    jeton = _jeton_de_reinitialisation("n@etude.ci")
    APIClient().post("/api/auth/forgot-password/reset", {"resetToken": jeton, "password": "Pirate!Mdp-2026xy"}, format="json")
    cache.clear()
    mail.outbox.clear()
    etape1 = APIClient().post("/api/auth/login", {"email": "n@etude.ci", "password": "Pirate!Mdp-2026xy"}, format="json")
    # … mais la connexion exige le code de l'application, et AUCUN code n'est envoyé par e-mail.
    assert etape1.status_code == 202 and etape1.data["method"] == "totp"
    assert not any("code de vérification" in m.body for m in mail.outbox)
    assert APIClient().post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": "123456", "challenge": etape1.data["challenge"]}, format="json").status_code == 400


@pytest.mark.django_db
@override_settings(**CLE_TOTP)
def test_le_code_d_application_exige_la_premiere_etape_et_ne_se_rejoue_pas():
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!Notaire-2026", role="admin")
    c = APIClient(); c.force_authenticate(notaire)
    secret, codes = _activer_totp(c, "Mdp!Notaire-2026")
    code = _code_suivant(secret, 1)
    anonyme = APIClient()
    # Sans ticket de défi (donc sans le mot de passe) : refus, même avec le bon code.
    assert anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": code}, format="json").status_code == 400
    defi = anonyme.post("/api/auth/login", {"email": "n@etude.ci", "password": "Mdp!Notaire-2026"}, format="json").data["challenge"]
    ok = anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": code, "challenge": defi}, format="json")
    assert ok.status_code == 200 and ok.data["token"]
    defi2 = anonyme.post("/api/auth/login", {"email": "n@etude.ci", "password": "Mdp!Notaire-2026"}, format="json").data["challenge"]
    assert anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": code, "challenge": defi2}, format="json").status_code == 400, \
        "un code déjà accepté ne se rejoue pas"
    # Code de secours : valable une seule fois.
    assert anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "recoveryCode": codes[0], "challenge": defi2}, format="json").status_code == 200
    defi3 = anonyme.post("/api/auth/login", {"email": "n@etude.ci", "password": "Mdp!Notaire-2026"}, format="json").data["challenge"]
    assert anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "recoveryCode": codes[0], "challenge": defi3}, format="json").status_code == 400


@pytest.mark.django_db
@override_settings(**CLE_TOTP)
def test_les_echecs_de_code_verrouillent_le_compte():
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!Notaire-2026", role="admin")
    c = APIClient(); c.force_authenticate(notaire)
    _activer_totp(c, "Mdp!Notaire-2026")
    anonyme = APIClient()
    defi = anonyme.post("/api/auth/login", {"email": "n@etude.ci", "password": "Mdp!Notaire-2026"}, format="json").data["challenge"]
    for _ in range(5):
        anonyme.post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": "000000", "challenge": defi}, format="json")
    notaire.refresh_from_db()
    assert notaire.locked_until is not None


@pytest.mark.django_db
@override_settings(**CLE_TOTP)
def test_la_desactivation_exige_mot_de_passe_et_code_et_previent_les_notaires():
    from notifications.models import Notification
    notaire = User.objects.create_user(email="n@etude.ci", password="Mdp!Notaire-2026", role="admin")
    autre = User.objects.create_user(email="n2@etude.ci", password="Mdp!Notaire-2026", role="admin")
    c = APIClient(); c.force_authenticate(notaire)
    secret, _ = _activer_totp(c, "Mdp!Notaire-2026")
    assert c.post("/api/auth/totp/disable", {"password": "Mdp!Notaire-2026", "code": "000000"}, format="json").status_code == 400
    assert c.post("/api/auth/totp/disable", {"password": "Mdp!Notaire-2026", "code": _code_suivant(secret, 1)}, format="json").status_code == 200
    assert Notification.objects.filter(recipient=autre, title="Second facteur affaibli").exists()
    assert c.get("/api/auth/totp").data["enabled"] is False


@pytest.mark.django_db
def test_sans_application_le_notaire_garde_le_code_par_e_mail_et_reçoit_un_rappel():
    from notifications.models import Notification
    User.objects.create_user(email="n@etude.ci", password="Mdp!Notaire-2026", role="admin")
    etape1 = APIClient().post("/api/auth/login", {"email": "n@etude.ci", "password": "Mdp!Notaire-2026"}, format="json")
    assert etape1.status_code == 202 and etape1.data["method"] == "email"
    code = _code_otp_envoye()
    ok = APIClient().post("/api/auth/mfa/verify", {"email": "n@etude.ci", "code": code, "challenge": etape1.data["challenge"]}, format="json")
    assert ok.status_code == 200
    assert Notification.objects.filter(title="Renforcez votre connexion").exists()


@pytest.mark.django_db
def test_un_ancien_jeton_sans_empreinte_est_refuse():
    from django.core import signing
    User.objects.create_user(email="co@etude.ci", password="Mdp!Initial-2026", role="collaborateur")
    forge = signing.dumps({"email": "co@etude.ci"}, salt="password-reset")
    assert APIClient().post("/api/auth/forgot-password/reset", {"resetToken": forge, "password": "Nouveau!Mdp-2026x"}, format="json").status_code == 400


def test_chaque_processus_de_production_declare_l_antivirus():
    """Trouvé par le test local Docker : seul `web` déclarait l'antivirus ;
    les exécutants refusaient donc de démarrer en production."""
    import yaml
    from tests.test_bloquants_production import BACKEND
    compose = yaml.safe_load((BACKEND / "docker-compose.yml").read_text(encoding="utf-8"))
    for nom, service in compose["services"].items():
        env = service.get("environment") or {}
        if env.get("DJANGO_SETTINGS_MODULE") == "ged_backend.settings.prod":
            assert env.get("ANTIVIRUS_HOST") == "clamav", f"{nom} : ANTIVIRUS_HOST absent"
