"""Bloquants de production levés — non-régression.

Chaque section correspond à un bloquant du rapport d'audit pré-production.
"""
import pytest
from django.core.cache import cache
from django.test import Client, override_settings
from rest_framework.test import APIClient

from accounts.models import User


@pytest.fixture(autouse=True)
def _cache_propre():
    cache.clear()
    yield
    cache.clear()


def _connecte(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


# ---------------------------------------------------------------------------
# Bloquant 5 — la limitation de débit ne bloque plus le travail normal
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_plusieurs_postes_derriere_la_meme_adresse_travaillent_sans_429():
    """Derrière le NAT d'une étude, tous les postes partagent une adresse :
    le budget anti-force-brute (20/min par adresse) bloquait l'étude entière."""
    postes = [User.objects.create_user(email=f"u{i}@etude.ci", password="Mdp!123456", role="clerc") for i in range(4)]
    codes = []
    for _ in range(15):
        for user in postes:
            codes.append(_connecte(user).get("/api/documents", HTTP_X_REAL_IP="203.0.113.7").status_code)
    assert codes.count(429) == 0, f"{codes.count(429)} requêtes refusées sur {len(codes)}"


@pytest.mark.django_db
def test_le_budget_d_un_compte_reste_borne_et_n_affecte_pas_les_autres():
    from rest_framework.settings import api_settings
    taux = dict(api_settings.DEFAULT_THROTTLE_RATES, user="5/minute")
    a = User.objects.create_user(email="a@etude.ci", password="Mdp!123456", role="clerc")
    b = User.objects.create_user(email="b@etude.ci", password="Mdp!123456", role="clerc")
    from django.conf import settings
    with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "DEFAULT_THROTTLE_RATES": taux}):
        codes_a = [_connecte(a).get("/api/documents").status_code for _ in range(7)]
        code_b = _connecte(b).get("/api/documents").status_code
    assert codes_a[:5] == [200] * 5 and 429 in codes_a[5:], "le budget par compte doit s'appliquer"
    assert code_b == 200, "le quota d'un collègue ne pénalise pas les autres"


@pytest.mark.django_db
def test_la_connexion_reste_limitee_par_adresse():
    anonyme = Client()
    codes = [anonyme.post("/api/auth/login", {"email": f"x{i}@x.ci", "password": "x"}, content_type="application/json",
                          HTTP_X_REAL_IP="203.0.113.9").status_code for i in range(25)]
    assert 429 in codes, "la force brute sur la connexion doit rester freinée"


@pytest.mark.django_db
def test_une_nouvelle_route_publique_herite_du_regime_strict():
    """Toute vue `AllowAny` est limitée par adresse, sans déclaration : un
    oubli ne peut pas ouvrir une route publique à la force brute."""
    from accounts.throttles import EtudeRateThrottle, route_publique
    from accounts.views import ForgotResetView, MFAVerifyView
    from documents.views import DocumentsView
    assert route_publique(MFAVerifyView) and route_publique(ForgotResetView)
    assert not route_publique(DocumentsView)
    from django.conf import settings
    assert settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] == ("accounts.throttles.EtudeRateThrottle",)
    assert EtudeRateThrottle


@pytest.mark.django_db(transaction=True)
def test_les_compteurs_fonctionnent_avec_le_cache_partage_de_production():
    _test_cache_partage()


# ---------------------------------------------------------------------------
# Bloquants 1 et 2 — HTTPS obligatoire, domaine exact, pas de tunnel temporaire
# ---------------------------------------------------------------------------

import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
PROD_SAINE = {
    "DJANGO_SETTINGS_MODULE": "ged_backend.settings.prod",
    "SECRET_KEY": "x" * 60,
    "ALLOWED_HOSTS": "ged.etude-exemple.ci,localhost",
    "CSRF_TRUSTED_ORIGINS": "https://ged.etude-exemple.ci",
    "CORS_ALLOWED_ORIGINS": "",
    "DOCUMENT_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    "EMAIL_BACKEND": "django.core.mail.backends.console.EmailBackend",
    "SECURE_SSL_REDIRECT": "true",
    "GED_ALLOW_INSECURE_HTTP": "false",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://ged.etude-exemple.ci/auth/oauth/google/callback",
    "DATABASE_URL": "sqlite:///:memory:",
    "ANTIVIRUS_HOST": "clamav",
}


def _demarrer_prod(**surcharge) -> subprocess.CompletedProcess:
    env = {**os.environ, **PROD_SAINE, **surcharge, "PYTHONIOENCODING": "utf-8"}
    code = ("import django; django.setup(); from django.conf import settings; "
            "print(settings.SECURE_SSL_REDIRECT, settings.SESSION_COOKIE_SECURE, settings.SECURE_HSTS_SECONDS, settings.SECURE_HSTS_PRELOAD)")
    return subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def test_une_configuration_de_production_saine_demarre_en_https():
    resultat = _demarrer_prod()
    assert resultat.returncode == 0, resultat.stderr[-800:]
    assert resultat.stdout.split() == ["True", "True", "31536000", "False"]


@pytest.mark.parametrize("surcharge, motif", [
    ({"SECURE_SSL_REDIRECT": "false"}, "SECURE_SSL_REDIRECT=false"),
    ({"ALLOWED_HOSTS": "localhost,127.0.0.1,.trycloudflare.com"}, "trycloudflare"),
    ({"ALLOWED_HOSTS": "*"}, "génériques"),
    ({"CSRF_TRUSTED_ORIGINS": "https://*.trycloudflare.com"}, "trycloudflare"),
    ({"CSRF_TRUSTED_ORIGINS": "http://ged.etude-exemple.ci"}, "non HTTPS"),
    ({"GOOGLE_OAUTH_REDIRECT_URI": "https://abc.trycloudflare.com/auth/oauth/google/callback"}, "tunnel temporaire"),
    ({"DOCUMENT_ENCRYPTION_KEY": "", "DOCUMENT_ENCRYPTION_KEYS": ""}, "DOCUMENT_ENCRYPTION_KEY"),
])
def test_la_production_refuse_de_demarrer_mal_configuree(surcharge, motif):
    resultat = _demarrer_prod(**surcharge)
    assert resultat.returncode != 0, "la production a démarré avec une configuration dangereuse"
    assert "ConfigurationProductionRefusee" in resultat.stderr and motif in resultat.stderr


def test_la_derogation_d_essai_local_reste_explicite():
    resultat = _demarrer_prod(SECURE_SSL_REDIRECT="false", GED_ALLOW_INSECURE_HTTP="true", ALLOWED_HOSTS="localhost")
    assert resultat.returncode == 0, resultat.stderr[-800:]
    assert "ESSAI LOCAL" in resultat.stderr


def test_le_trousseau_seul_suffit_a_demarrer():
    resultat = _demarrer_prod(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS='{"k2026": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="}')
    assert resultat.returncode == 0, resultat.stderr[-800:]


def test_seul_caddy_est_expose_et_nginx_ne_fait_confiance_qu_a_lui():
    import yaml
    compose = yaml.safe_load((BACKEND / "docker-compose.yml").read_text(encoding="utf-8"))
    exposes = {nom for nom, service in compose["services"].items() if service.get("ports")}
    assert exposes == {"caddy"}, f"services exposés : {exposes}"
    assert compose["services"]["caddy"]["networks"]["bordure"]["ipv4_address"] == "172.28.0.10"
    assert all(s.get("logging") for s in compose["services"].values()), "rotation des journaux manquante"
    conf = (BACKEND / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    assert "set_real_ip_from 172.28.0.10;" in conf and conf.count("set_real_ip_from") == 1
    assert "X-Forwarded-Proto $scheme" not in conf, "le protocole doit venir de Caddy"
    caddy = (BACKEND / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "{$GED_DOMAIN}" in caddy and "reverse_proxy nginx:80" in caddy and "Strict-Transport-Security" in caddy


@pytest.mark.django_db
def test_la_sonde_de_sante_n_est_pas_redirigee_en_https():
    with override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^api/health$"], ALLOWED_HOSTS=["localhost"]):
        assert Client().get("/api/health", HTTP_HOST="localhost").status_code == 200
        assert Client().get("/api/documents", HTTP_HOST="localhost").status_code == 301


# ---------------------------------------------------------------------------
# Bloquant 3 — renouvellement des secrets et rechiffrement des pièces
# ---------------------------------------------------------------------------

import base64
import hashlib
import io
import json

CLE_A = base64.urlsafe_b64encode(b"A" * 32).decode()
CLE_B = base64.urlsafe_b64encode(b"B" * 32).decode()
PDF = b"%PDF-1.4\n% acte\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _piece_chiffree(reference, notaire, key_id=None, contenu=PDF):
    from django.core.files.base import ContentFile
    from documents.crypto import encrypt
    from documents.models import Document
    from dossiers.models import Dossier
    dossier, _ = Dossier.objects.get_or_create(reference="SUC-2026-00077", defaults={"domaine": "SUC", "nom": "D", "client": "C", "created_by": notaire})
    chiffre, kid = encrypt(contenu, key_id=key_id)
    doc = Document.objects.create(reference=reference, dossier=dossier, type="Acte", nom=reference, original_filename="a.pdf",
                                  content_type="application/pdf", size_bytes=len(PDF), sha256=hashlib.sha256(PDF).hexdigest(),
                                  uploaded_by=notaire, master_reference=reference, encryption_key_id=kid)
    doc.fichier.save(f"{reference}.pdf", ContentFile(chiffre), save=True)
    return doc


@pytest.mark.django_db
def test_le_rechiffrement_bascule_toutes_les_pieces_sur_la_nouvelle_cle():
    from django.core.management import call_command
    from audit.models import AuditLog
    from documents.models import Document
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    anciennes = {"legacy": CLE_A}
    with override_settings(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=json.dumps(anciennes), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="legacy"):
        docs = [_piece_chiffree(f"DOC_R{i}", notaire) for i in range(3)]
    anciens_fichiers = [d.fichier.name for d in docs]
    trousseau = json.dumps({"legacy": CLE_A, "k2026": CLE_B})
    with override_settings(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=trousseau, DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="k2026"):
        sortie = io.StringIO()
        call_command("rechiffrer_documents", "--simulation", stdout=sortie)
        assert "Pièces à rechiffrer : 3" in sortie.getvalue()
        assert Document.objects.filter(encryption_key_id="legacy").count() == 3, "la simulation n'écrit rien"
        call_command("rechiffrer_documents", stdout=io.StringIO())
        for doc in Document.objects.filter(reference__startswith="DOC_R"):
            assert doc.encryption_key_id == "k2026"
            assert doc.decrypted_bytes() == PDF
        storage = docs[0].fichier.storage
        assert not any(storage.exists(n) for n in anciens_fichiers), "les anciens chiffrés sont supprimés après bascule"
        assert AuditLog.objects.filter(action="document_reencrypted").count() == 3
        sortie = io.StringIO()
        call_command("rechiffrer_documents", stdout=sortie)
        assert "0 pièce(s) rechiffrée(s)" in sortie.getvalue(), "une relance ne refait rien"
    # Sans l'ancienne clé, les pièces restent lisibles : elles ne dépendent plus d'elle.
    with override_settings(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=json.dumps({"k2026": CLE_B}), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="k2026"):
        assert Document.objects.get(reference="DOC_R0").decrypted_bytes() == PDF


@pytest.mark.django_db
def test_une_piece_alteree_n_est_jamais_reecrite():
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from documents.models import Document
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    with override_settings(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=json.dumps({"legacy": CLE_A}), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="legacy"):
        alteree = _piece_chiffree("DOC_ALT", notaire, contenu=PDF + b"falsifie")
    nom = alteree.fichier.name
    with override_settings(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=json.dumps({"legacy": CLE_A, "k2026": CLE_B}), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="k2026"):
        with pytest.raises(CommandError, match="anomalie"):
            call_command("rechiffrer_documents", stdout=io.StringIO(), stderr=io.StringIO())
    alteree.refresh_from_db()
    assert alteree.encryption_key_id == "legacy" and alteree.fichier.name == nom and alteree.fichier.storage.exists(nom)


def test_la_preparation_des_secrets_regenere_sans_rien_afficher(tmp_path):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    source = tmp_path / ".env.prod"
    source.write_text("SECRET_KEY=ancien-secret-partage\nDOCUMENT_ENCRYPTION_KEY=" + CLE_A + "\nPOSTGRES_PASSWORD=ancienmdp\n"
                      "EMAIL_HOST_PASSWORD=gmail-ancien\nEMAIL_HOST_USER=etude@gmail.com\nPOSTGRES_USER=ged_user\n", encoding="utf-8")
    sortie = tmp_path / ".env.prod.nouveau"
    console = io.StringIO()
    call_command("preparer_secrets_production", "--source", str(source), "--sortie", str(sortie), stdout=console)
    texte = sortie.read_text(encoding="utf-8")
    valeurs = dict(l.split("=", 1) for l in texte.splitlines() if l and not l.startswith("#"))
    assert valeurs["SECRET_KEY"] not in ("", "ancien-secret-partage") and len(valeurs["SECRET_KEY"]) > 50
    assert valeurs["POSTGRES_PASSWORD"] != "ancienmdp"
    trousseau = json.loads(valeurs["DOCUMENT_ENCRYPTION_KEYS"])
    assert trousseau["legacy"] == CLE_A, "l'ancienne clé reste disponible pour le rechiffrement"
    assert trousseau[valeurs["DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID"]] != CLE_A
    assert valeurs["EMAIL_HOST_PASSWORD"] == "A_REMPLACER" and valeurs["EMAIL_HOST_USER"] == "etude@gmail.com"
    assert valeurs["CREATE_INITIAL_ADMIN"] == "false" and valeurs["SECURE_SSL_REDIRECT"] == "true"
    for secret in (valeurs["SECRET_KEY"], valeurs["POSTGRES_PASSWORD"], CLE_A, "ancienmdp"):
        assert secret not in console.getvalue(), "aucun secret ne doit s'afficher"
    assert "ancien-secret-partage" not in texte
    with pytest.raises(CommandError):
        call_command("preparer_secrets_production", "--source", str(source), "--sortie", str(sortie))
    with pytest.raises(CommandError):
        call_command("preparer_secrets_production", "--source", str(source), "--sortie", str(source), "--ecraser")


def test_la_production_refuse_une_valeur_a_remplacer():
    resultat = _demarrer_prod(EMAIL_HOST_PASSWORD="A_REMPLACER")
    assert resultat.returncode != 0 and "EMAIL_HOST_PASSWORD" in resultat.stderr


# ---------------------------------------------------------------------------
# Bloquant 4 — PRA : restauration réelle, copie hors site, séquestre des clés
# ---------------------------------------------------------------------------

class FauxS3:
    """Stockage S3 en mémoire (mêmes appels que boto3)."""
    def __init__(self, taille_faussee=False):
        self.objets, self.envois, self.taille_faussee = {}, [], taille_faussee

    def upload_file(self, chemin, bucket, key):
        self.objets[key] = Path(chemin).read_bytes()
        self.envois.append(key)

    def head_object(self, Bucket, Key):
        if Key not in self.objets:
            raise KeyError(Key)
        return {"ContentLength": len(self.objets[Key]) + (1 if self.taille_faussee else 0)}

    def download_file(self, bucket, key, chemin):
        Path(chemin).write_bytes(self.objets[key])

    def list_objects_v2(self, Bucket, Prefix, **_):
        return {"Contents": [{"Key": k} for k in sorted(self.objets) if k.startswith(Prefix)], "IsTruncated": False}


def _etude_a_sauvegarder(tmp_path, settings):
    settings.BACKUP_LOCAL_ROOT = str(tmp_path / "sauvegardes")
    notaire = User.objects.create_user(email="notaire@etude.ci", password="Mdp!Restaure-2026", role="admin", first_name="Awa")
    docs = [_piece_chiffree(f"DOC_P{i}", notaire) for i in range(3)]
    from audit.services import log_system_event
    for i in range(3):
        log_system_event("evenement_de_test", "test", str(i))
    return notaire, docs


def _sinistre():
    """Perte totale : base vidée (tables recréées vides, comme après
    `migrate` sur un serveur neuf) et fichiers des pièces effacés."""
    from django.core.files.storage import default_storage
    from django.core.management import call_command
    from documents.models import Document
    noms = list(Document.objects.values_list("fichier", flat=True))
    call_command("flush", interactive=False, verbosity=0)
    for nom in noms:
        if nom and default_storage.exists(nom):
            default_storage.delete(nom)
    return noms


CHIFFREMENT = dict(DOCUMENT_ENCRYPTION_KEY="", DOCUMENT_ENCRYPTION_KEYS=json.dumps({"legacy": CLE_A}), DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID="legacy")


@pytest.mark.django_db(transaction=True)
@override_settings(**CHIFFREMENT)
def test_la_ged_se_restaure_integralement_apres_un_sinistre(tmp_path, settings):
    from django.core.files.storage import default_storage
    from django.core.management import call_command
    from audit.models import AuditLog
    from documents.models import Document
    from settings_app.backup_service import run_backup
    notaire, docs = _etude_a_sauvegarder(tmp_path, settings)
    run = run_backup(notaire)
    journal_avant = AuditLog.objects.count()
    noms = _sinistre()
    assert not User.objects.exists() and not any(default_storage.exists(n) for n in noms)

    sortie = io.StringIO()
    call_command("restaurer_sauvegarde", "--dossier", run.local_path, stdout=sortie)
    assert "Restauration terminée et vérifiée" in sortie.getvalue()
    assert Document.objects.count() == 3
    for doc in Document.objects.all():
        assert doc.decrypted_bytes() == PDF, "chaque pièce restaurée se déchiffre à l'identique"
    assert AuditLog.objects.count() >= journal_avant
    from audit.services import verify_chain
    assert verify_chain()["ok"], "la chaîne d'audit restaurée reste intègre"
    # Le notaire se reconnecte avec son mot de passe d'origine.
    cache.clear()
    reponse = Client().post("/api/auth/login", {"email": "notaire@etude.ci", "password": "Mdp!Restaure-2026"}, content_type="application/json")
    assert reponse.status_code in (200, 202), reponse.content
    # Une nouvelle pièce après restauration reçoit un identifiant libre.
    _piece_chiffree("DOC_APRES", User.objects.get(email="notaire@etude.ci"))


@pytest.mark.django_db(transaction=True)
@override_settings(**CHIFFREMENT)
def test_la_restauration_refuse_d_ecraser_une_base_vivante(tmp_path, settings):
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from settings_app.backup_service import run_backup
    notaire, _ = _etude_a_sauvegarder(tmp_path, settings)
    run = run_backup(notaire)
    with pytest.raises(CommandError, match="contient déjà des données"):
        call_command("restaurer_sauvegarde", "--dossier", run.local_path, stdout=io.StringIO())


@pytest.mark.django_db(transaction=True)
@override_settings(**CHIFFREMENT, BACKUP_CLOUD_ENABLED=True, BACKUP_CLOUD_BUCKET="ged-etude", BACKUP_CLOUD_ACCESS_KEY="a", BACKUP_CLOUD_SECRET_KEY="b")
def test_la_copie_hors_site_est_verifiee_incrementale_et_restaurable(tmp_path, settings, monkeypatch):
    from django.core.management import call_command
    from documents.models import Document
    from settings_app import backup_service
    s3 = FauxS3()
    monkeypatch.setattr(backup_service, "_cloud_client", lambda: s3)
    notaire, _ = _etude_a_sauvegarder(tmp_path, settings)
    premiere = backup_service.run_backup(notaire)
    assert premiere.cloud_status == "success"
    envois_initiaux = len(s3.envois)
    assert envois_initiaux == 3 + 3, "3 pièces + base + 2 manifestes"
    backup_service.run_backup(notaire)
    assert len(s3.envois) - envois_initiaux == 3, "les pièces déjà hors site ne sont pas renvoyées"
    assert not any("Testament" in k for k in s3.objets)

    _sinistre()
    import shutil
    shutil.rmtree(settings.BACKUP_LOCAL_ROOT)  # le serveur ET son disque de sauvegarde sont perdus
    liste = io.StringIO()
    call_command("restaurer_sauvegarde", "--cloud", "liste", stdout=liste)
    assert len(liste.getvalue().split()) == 2
    call_command("restaurer_sauvegarde", "--cloud", "derniere", stdout=io.StringIO())
    assert Document.objects.count() == 3
    assert all(d.decrypted_bytes() == PDF for d in Document.objects.all())


@pytest.mark.django_db
@override_settings(**CHIFFREMENT, BACKUP_CLOUD_ENABLED=True, BACKUP_CLOUD_BUCKET="ged-etude", BACKUP_CLOUD_ACCESS_KEY="a", BACKUP_CLOUD_SECRET_KEY="b")
def test_une_copie_hors_site_non_conforme_fait_echouer_la_sauvegarde(tmp_path, settings, monkeypatch):
    from settings_app import backup_service
    from settings_app.models import BackupRun
    monkeypatch.setattr(backup_service, "_cloud_client", lambda: FauxS3(taille_faussee=True))
    notaire, _ = _etude_a_sauvegarder(tmp_path, settings)
    with pytest.raises(RuntimeError, match="non conforme"):
        backup_service.run_backup(notaire)
    assert BackupRun.objects.filter(kind="backup", status=BackupRun.Status.FAILURE).exists()


@pytest.mark.django_db
@override_settings(**CHIFFREMENT)
def test_une_cle_non_sauvegardee_hors_serveur_declenche_une_alerte_critique():
    from notifications.models import Notification
    from settings_app.tasks import cles_non_sequestrees, sequestre_cles
    notaire = User.objects.create_user(email="n@e.ci", password="Mdp!123456", role="admin")
    assert cles_non_sequestrees() == ["legacy"]
    sequestre_cles()
    alerte = Notification.objects.get(recipient=notaire, type="key_escrow_missing")
    assert alerte.severity == "critique"
    reponse = _connecte(notaire).post("/api/key-management/recovery-export", {"passphrase": "phrase-secrete-tres-longue"}, format="json")
    assert reponse.status_code == 200
    assert cles_non_sequestrees() == [], "l'export du paquet couvre les clés en service"


def _test_cache_partage():
    """En production, les compteurs vivent dans le cache en base (partagé par
    les processus gunicorn). On le vérifie avec ce backend réel."""
    from django.core.management import call_command
    from django.core.cache import caches
    base = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "ged_cache_test"}}
    with override_settings(CACHES=base):
        call_command("createcachetable", verbosity=0)
        caches["default"].clear()
        anonyme = Client()
        codes = [anonyme.post("/api/auth/login", {"email": "x@x.ci", "password": "x"}, content_type="application/json",
                              HTTP_X_REAL_IP="203.0.113.10").status_code for _ in range(22)]
        assert 429 in codes
        caches["default"].clear()
