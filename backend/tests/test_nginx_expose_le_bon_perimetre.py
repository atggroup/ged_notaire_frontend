"""La configuration nginx ne doit exposer que le frontal.

En production, `SERVE_FRONTEND=false` : c'est nginx qui sert les pages, pas
Django. Le garde-fou de `ged_backend/frontend.py` ne s'applique donc plus, et
le volume monté (`../:/usr/share/nginx/html:ro`) est la racine du dépôt —
`backend/.env.prod` compris, c'est-à-dire la clé de chiffrement documentaire,
le secret Django et le mot de passe de la base.

Ce test rejoue les règles de priorité de nginx sur le fichier réel :
  1. `location = /chemin`   — correspondance exacte, gagne tout de suite ;
  2. `location ^~ /prefixe` — préfixe prioritaire, coupe court aux regex ;
  3. `location ~ ` / `~* `  — regex, dans l'ordre d'apparition ;
  4. `location /prefixe`    — plus long préfixe, en dernier recours.
"""
import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parent.parent / "deploy" / "nginx.conf"


def _blocs():
    """Extrait (type, motif, corps-condensé) de chaque `location`."""
    texte = CONF.read_text(encoding="utf-8")
    motif = re.compile(r"location\s+(=|\^~|~\*|~)?\s*(\S+)\s*\{")
    blocs = []
    for m in motif.finditer(texte):
        modificateur, chemin = (m.group(1) or ""), m.group(2)
        # Corps : jusqu'à l'accolade fermante de même niveau (blocs simples ici).
        reste = texte[m.end():]
        profondeur, fin = 1, 0
        for i, c in enumerate(reste):
            if c == "{":
                profondeur += 1
            elif c == "}":
                profondeur -= 1
                if profondeur == 0:
                    fin = i
                    break
        blocs.append((modificateur, chemin, reste[:fin]))
    return blocs


def resoudre(chemin_demande):
    """Renvoie le corps du `location` que nginx retiendrait."""
    blocs = _blocs()
    # 1. correspondance exacte
    for mod, motif, corps in blocs:
        if mod == "=" and motif == chemin_demande:
            return corps
    # 2. préfixe prioritaire (le plus long l'emporte)
    prioritaires = [(motif, corps) for mod, motif, corps in blocs
                    if mod == "^~" and chemin_demande.startswith(motif)]
    if prioritaires:
        return max(prioritaires, key=lambda x: len(x[0]))[1]
    # 3. regex, dans l'ordre du fichier
    for mod, motif, corps in blocs:
        if mod in ("~", "~*"):
            drapeaux = re.IGNORECASE if mod == "~*" else 0
            if re.search(motif, chemin_demande, drapeaux):
                return corps
    # 4. plus long préfixe
    prefixes = [(motif, corps) for mod, motif, corps in blocs
                if mod == "" and chemin_demande.startswith(motif)]
    if prefixes:
        return max(prefixes, key=lambda x: len(x[0]))[1]
    return ""


REFUSES = [
    "/backend/.env",
    "/backend/.env.prod",
    "/backend/.env.prod.example",
    "/backend/ged_backend/settings/prod.py",
    "/backend/ged_backend/confidentialite.py",
    "/backend/manage.py",
    "/backend/docker-compose.yml",
    "/backend/backup_store/20260101-000000/database.json.enc",
    "/backend/media/documents/acte.pdf.enc",
    "/.git/config",
    "/.gitignore",
    "/.dockerignore",
    "/HANDOFF.md",
    "/CONFORMITE_RGPD_ARTCI.md",
    "/PRA_PCA.md",
    "/.claude/launch.json",
]

SERVIS = [
    "/login.html",
    "/notaire-admin/index.html",
    "/notaire-admin/document-detail.html",
    "/clerc-principal/assets/app.js",
    "/assets/responsive.css",
    "/assets/vendor/pdf.min.js",
    "/assets/vendor/pdf.worker.min.js",
    "/assets/img/ILLUSTRATION_CONNEXION.png",
    "/assets/video/intro-transition.mp4",
]


@pytest.mark.parametrize("chemin", REFUSES)
def test_les_fichiers_sensibles_sont_refuses(chemin):
    corps = resoudre(chemin)
    assert "return 404" in corps, (
        f"{chemin} serait servi par nginx — la configuration expose le dépôt"
    )


@pytest.mark.parametrize("chemin", SERVIS)
def test_le_frontal_reste_servi(chemin):
    corps = resoudre(chemin)
    assert "return 404" not in corps, f"{chemin} est bloqué alors qu'il est nécessaire"
    assert "try_files" in corps, f"{chemin} n'atteint pas la liste blanche du frontal"


@pytest.mark.parametrize("chemin", [
    "/api/documents", "/api/auth/login", "/api/dashboard/summary",
])
def test_l_api_est_bien_relayee(chemin):
    assert "proxy_pass" in resoudre(chemin)


def test_les_statiques_django_passent_par_leur_alias():
    """Sans `^~`, la liste blanche d'extensions capterait /static/…css et
    nginx irait le chercher dans le frontal au lieu de l'alias."""
    corps = resoudre("/static/admin/css/base.css")
    assert "alias /var/www/static/" in corps


def test_les_pages_portent_les_en_tetes_de_securite():
    """Django ne sert pas ces pages en production : ses en-têtes ne les
    couvrent pas, nginx doit les reposer."""
    corps = resoudre("/login.html")
    for en_tete in ("Content-Security-Policy", "X-Frame-Options",
                    "X-Content-Type-Options", "Referrer-Policy"):
        assert en_tete in corps, f"{en_tete} absent des pages servies par nginx"
    assert "script-src 'self';" in corps
    assert "script-src 'self' 'unsafe-inline'" not in corps


def test_la_racine_ne_sert_pas_un_fichier_arbitraire():
    corps = resoudre("/un-chemin-sans-extension")
    assert "return 302 /login.html" in corps


def test_django_est_resolu_a_chaque_requete_et_non_au_demarrage():
    """Un `proxy_pass http://web:8000` littéral fige l'adresse du conteneur au
    démarrage de nginx : après une mise à jour du service web, 502 jusqu'au
    redémarrage de nginx. Il faut le résolveur Docker et une variable."""
    conf = CONF.read_text(encoding="utf-8")
    assert "resolver 127.0.0.11" in conf
    assert "proxy_pass http://web:" not in conf, "adresse de Django figée au démarrage de nginx"
