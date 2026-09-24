# Backend GED Cabinet Notarial

API Django REST réelle, séparée du front existant. Le front n'a pas été modifié.

## Architecture

`accounts` gère les invitations nominatives, l'identité, JWT, MFA et OTP ; `documents` stocke les fichiers chiffrés, les contrôles qualité et les versions ; `dossiers` sépare clients, parties et affaires ; `permissions_app` les droits fins et demandes ; `audit` la traçabilité append-only ; `notifications` les alertes et tâches ; `settings_app` la configuration du cabinet. PostgreSQL est la cible de production, SQLite n'est qu'un repli de développement local si `DATABASE_URL` est absent.

Pour un démarrage sans aucune connaissance technique, voir `DEMARRAGE.md` à la racine du projet. Pour la connexion Google en détail, voir `GOOGLE_OAUTH.md` dans ce dossier.

## Installation

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Renseigner au minimum SECRET_KEY, DATABASE_URL et DOCUMENT_ENCRYPTION_KEY.
python manage.py migrate
python manage.py create_first_admin --email notaire@etude.ci
python manage.py runserver 127.0.0.1:8000
```

La commande `create_first_admin` n'est autorisée que si la table utilisateur est vide. Elle affiche une fois le mot de passe aléatoire, sauf si `INITIAL_ADMIN_PASSWORD` est fourni. Ne le conservez jamais dans un fichier versionné.

Générez la clé AES-256 hors ligne :

```powershell
python -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

## Base de données (PostgreSQL en dev)

Le projet utilise `django-environ` : `DATABASE_URL` pilote entièrement la connexion, aucune modification de code n'est nécessaire pour passer de SQLite à PostgreSQL. Sans `DATABASE_URL` dans `.env`, Django retombe sur un fichier SQLite local (`db.sqlite3`), pratique pour un tout premier essai mais à éviter dès qu'on teste des fonctionnalités réelles (verrous, contraintes, fuseaux horaires).

Installation locale (Linux/WSL) :

```bash
sudo apt-get install postgresql postgresql-contrib
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER ged_user WITH PASSWORD 'change-me';"
sudo -u postgres psql -c "CREATE DATABASE ged_notaire OWNER ged_user;"
# Requis pour que `pytest` puisse créer/détruire la base de test automatiquement :
sudo -u postgres psql -c "ALTER USER ged_user CREATEDB;"
```

Sous Windows, utiliser l'installeur officiel (postgresql.org) ou Docker Desktop avec l'image `postgres:16`.

Puis dans `.env` :

```
DATABASE_URL=postgresql://ged_user:change-me@127.0.0.1:5432/ged_notaire
```

```bash
python manage.py migrate
python manage.py create_first_admin --email notaire@etude.ci
pytest   # la suite (22 tests) doit passer à l'identique sur Postgres
```

Le droit `CREATEDB` n'est nécessaire qu'en développement, pour que `pytest`/`manage.py test` puisse créer sa base de test (`test_ged_notaire`) automatiquement ; en production, ce droit n'est pas requis car aucune base de test n'y est créée.

## Email (SMTP réel)

Par défaut (`EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend`), les codes MFA et les invitations s'affichent dans les logs du serveur au lieu d'être envoyés — pratique en dev, inutilisable en production.

Pour un envoi réel, dans `.env` :

```
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.votre-fournisseur.example
EMAIL_PORT=587
EMAIL_HOST_USER=votre-identifiant-smtp
EMAIL_HOST_PASSWORD=votre-mot-de-passe-ou-clé-API
EMAIL_USE_TLS=true
EMAIL_USE_SSL=false
DEFAULT_FROM_EMAIL=notifications@votre-domaine.example
```

`EMAIL_USE_TLS` (port 587, STARTTLS) et `EMAIL_USE_SSL` (port 465, SSL implicite) sont mutuellement exclusifs — n'activer qu'un seul des deux selon ce que documente le fournisseur.

Quelques fournisseurs courants :

- **Gmail / Google Workspace** : `smtp.gmail.com`, port 587, TLS. Nécessite un « mot de passe d'application » (pas le mot de passe du compte) — à créer depuis les paramètres de sécurité Google (2FA activée obligatoire).
- **Brevo (ex-Sendinblue)**, **SendGrid**, **Mailgun** : populaires en Afrique de l'Ouest, offrent un identifiant/clé API SMTP dédié dans leur tableau de bord — ne jamais utiliser le mot de passe du compte web.
- **OVH Mail** : `ssl0.ovh.net`, port 587 (TLS) ou 465 (SSL).

En production (`settings/prod.py`), si `EMAIL_BACKEND` est le backend SMTP, `EMAIL_HOST`, `EMAIL_HOST_USER` et `EMAIL_HOST_PASSWORD` sont désormais obligatoires : le serveur refuse de démarrer plutôt que d'échouer silencieusement au premier envoi de code MFA.

Pour vérifier l'envoi sans passer par l'interface :

```bash
python manage.py shell -c "from django.core.mail import send_mail; send_mail('Test GED', 'Ceci est un test.', None, ['votre-adresse@example.com'])"
```

## Connexion Google (identité, sans auto-provisionnement)

`GET /api/auth/oauth/google/start` redirige vers
l'écran de consentement Google. Google renvoie ensuite le navigateur vers
`GET /auth/oauth/google/callback` (hors `/api/`, à l'adresse exacte
déclarée dans Google Cloud Console — voir `GOOGLE_OAUTH.md`), qui :

- connecte directement la personne si un compte existe déjà pour son
  e-mail (classique ou déjà lié à Google), sans jamais créer de doublon ;
- refuse un e-mail sans compte activé par invitation ; Google prouve une
  identité, il ne confère jamais un rôle ni un accès documentaire.

Le jeton final n'apparaît jamais dans l'URL affichée : le callback renvoie
un billet signé à usage unique consommé par `POST
/api/auth/oauth/google/consume`. La vérification du jeton d'identité
Google (signature RS256, émetteur, audience, expiration) est faite
localement dans `accounts/google_oauth.py`, sans dépendance externe.

## Sécurité et exploitation

- Les mots de passe sont hachés Argon2id ; les tokens JWT expirent en 8 h,
  peuvent être révoqués immédiatement et les comptes sont verrouillés après
  cinq échecs. Les comptes Notaire/Admin exigent un second facteur : application d'authentification (TOTP, recommandée, Profil → Sécurité) ou, à défaut, code par e-mail. La réinitialisation du mot de passe ferme toutes les sessions et son lien est à usage unique.
- Un utilisateur est créé uniquement après une invitation nominative,
  expirante et à usage unique créée par le notaire. La désactivation révoque
  toutes ses sessions.
- Les OTP sont hachés, à usage unique, expirent après 10 min, limités à 5 tentatives et à un envoi par 45 s. En production, configurez SMTP ; le backend console n'est qu'un réglage de développement.
- Les octets de chaque document sont chiffrés AES-256-GCM sur disque avec un **trousseau de clés versionné** (`DOCUMENT_ENCRYPTION_KEY` en développement simple, ou `DOCUMENT_ENCRYPTION_KEYS` + `DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID` pour permettre une rotation planifiée sans jamais rendre un document existant illisible — chaque document conserve l'identifiant de la clé qui l'a chiffré). `MEDIA_ROOT` ne contient donc pas les originaux en clair. Sauvegardez les clés dans un gestionnaire de secrets, avec rotation planifiée et copie de secours hors ligne — voir `CONFORMITE_RGPD_ARTCI.md` (§7) et `PRA_PCA.md` (§5). La lecture et l'export déchiffrent en mémoire uniquement après autorisation.
- Après dépôt, l'OCR extrait le texte lorsqu'un moteur local est disponible (`pypdf` pour les PDF textuels, Tesseract pour les scans image). Son état est exposé, une relance est possible, et son résultat reste une aide de recherche soumise au contrôle humain.
- Une suppression est récupérable : `corbeille → demande de destruction → autorisation`. Aucune purge binaire automatique n'est fournie : les durées légales de conservation doivent être définies et autorisées par le cabinet avant toute destruction définitive.
- Pour HTTPS, utilisez `ged_backend.settings.prod` derrière un proxy TLS (Nginx/Caddy), envoyez `X-Forwarded-Proto`, et servez uniquement HTTPS. Les cookies sécurisés, HSTS et la redirection HTTPS sont activés en production.
- En V1 la vérification PDF contrôle le vrai en-tête PDF. La conformité PDF/A ne peut pas être établie uniquement par MIME : ajoutez veraPDF dans la chaîne d'ingestion si le cabinet exige une validation normative PDF/A stricte.

## Conformité et continuité d'activité

Deux documents complètent ce backend, à la racine du projet :

- `CONFORMITE_RGPD_ARTCI.md` : finalités des traitements, catégories de
  données, durées de conservation, sous-traitants, gestion des clés de
  chiffrement, incidents et droits des personnes — au regard de la loi
  ivoirienne n° 2013-450 et de l'ARTCI. À faire valider par le conseil du
  cabinet ; ce n'est pas un avis juridique.
- `PRA_PCA.md` : plan de reprise et de continuité d'activité (que faire si
  le serveur tombe en panne), architecture de sauvegarde recommandée
  (copie locale + hors site + copie isolée/immuable), et procédure de
  récupération d'urgence des clés de chiffrement.

## Tableau de bord par rôle

`GET /api/dashboard/summary` retourne, en plus des compteurs génériques,
des signaux propres au rôle connecté : dossiers sensibles (confidentiels ou
très confidentiels) et dossiers gelés (`legalHold`) visibles par
l'utilisateur, pièces de checklist obligatoires encore manquantes, et
échéances de tâches à venir. Pour le notaire/admin uniquement, la réponse
inclut en plus `alertesSecurite` (comptes verrouillés récemment, pics
d'échecs de connexion, comptes connectés depuis plusieurs adresses IP
distinctes sur les dernières 24 h — un signal à vérifier, pas une preuve,
au regard de l'interdiction des comptes partagés) et `chiffrement` (clé de
chiffrement active et clés connues du trousseau, jamais leur valeur).

## Sauvegardes

La page **Sauvegarde** lit maintenant les volumes réellement enregistrés et
conserve l'historique de chaque test d'intégrité. Un test déchiffre chaque
document, recalcule son empreinte SHA-256 et n'écrit jamais dans les données
de production. Activez `BACKUP_CLOUD_ENABLED=true` uniquement lorsqu'un outil
de réplication externe est effectivement configuré ; l'interface affiche
explicitement « non configurée » tant que cette étape n'est pas réalisée.

## Automatisations

Les travaux automatiques tournent dans deux exécutants (`python manage.py
run_worker`), services `travaux` et `sauvegarde` du `docker-compose.yml`.
Chaque travail est verrouillé (un seul exécutant à la fois), tracé
(`JobRun`, écran **Sauvegarde · Supervision**), journalisé dans l'audit dès
qu'il agit ou échoue, retenté selon sa politique, et **tout échec est notifié
aux notaires**. L'échéancier vit en base : un redémarrage ne fait ni perdre ni
rejouer une échéance.

| Travail | Fréquence | Effet |
|---|---|---|
| `rappels_taches` | 5 min | Paliers J-7 / J-3 / J-1 / jour J, retard (assigné + donneur d'ordre), escalade J+2 au notaire superviseur, échéances d'affectation |
| `envoi_emails` | 1 min | File des e-mails de notification, 5 tentatives (1 → 240 min), échec définitif signalé |
| `ocr` | 1 min | File OCR : prise en charge atomique, reprise des traitements bloqués, 3 tentatives, échec notifié |
| `suivi_dossiers` | 07:00 | Relance des pièces requises non reçues (tâche automatique unique), pièces datées expirées/bientôt expirées, originaux papier non rendus |
| `surveillance_securite` | 5 min | Force brute, vague d'échecs, verrouillages, IP multiples, consultation/export massifs, exports hors horaires, corbeille en série, privilèges sensibles |
| `integrite_audit` | 03:30 | Vérification de la chaîne de hash ; rupture = alerte critique |
| `nettoyage` | 03:00 | OTP expirés, inscriptions/invitations périmées, notifications lues anciennes, historique des travaux — **jamais** de document, d'audit ni de sauvegarde |
| `sauvegarde` | 6 h | Sauvegarde chiffrée + rotation (24 h / 7 j / 4 sem. / 12 mois), 2 relances |
| `fraicheur_sauvegarde` | 1 h | Alerte critique si aucune sauvegarde réussie récente |
| `exercice_pra` | dimanche 04:00 | Test de restauration non destructif enregistré et notifié |

```bash
python manage.py run_worker --liste           # échéancier
python manage.py run_worker --travail ocr     # exécution immédiate
python manage.py charger_modeles_checklist    # modèles de checklist proposés
```

Garde-fous : aucune automatisation ne coche une pièce, ne valide, n'archive,
ne supprime un document ni ne suspend un compte. Elles signalent, proposent et
créent des tâches ; les décisions juridiques restent humaines.

**Checklists** : les modèles (`dossiers/modeles_checklist.json`) sont une
*proposition* à valider par le notaire (admin Django ou
`/api/checklist-templates`). À la création d'un dossier, les modèles actifs du
domaine génèrent sa checklist ; un dépôt dont le type correspond est rattaché
à l'élément (« reçu, à vérifier ») — la coche reste humaine.

## Brancher le front

Dans `assets/auth-config.js` et les trois `assets/config.js`, renseignez `useMock: false` et `apiBase: "http://127.0.0.1:8000/api"`, sans préfixe supplémentaire. CORS doit contenir l'origine du serveur statique dans `CORS_ALLOWED_ORIGINS`.

Pour un démarrage local simple, le serveur Django sert également le front sur `http://127.0.0.1:8000/login.html` (même origine, donc sans CORS). Ce mode est contrôlé par `SERVE_FRONTEND`; désactivez-le en production et servez le front via Nginx/Caddy.

Le front fourni appelle toutes les routes JSON contractuelles. Ses pages `scan.html` sont toutefois purement statiques : elles ne contiennent actuellement aucun `<input type="file">`, `FormData`, ni appel multipart. Sans modifier ce front (contrainte de ce projet), l'upload réel se fait via `POST /api/documents` ou `POST /api/documents/upload`, avec un champ `fichier` (ou `file`/`document`) et `type`, `niveau`, `dossier` facultatif. Le backend est prêt pour ce branchement dès qu'un contrôle de fichier frontend sera autorisé.

Exemple :

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/documents/upload -H "Authorization: Bearer <token>" -F "fichier=@C:\scan.jpg" -F "type=Vente" -F "niveau=Restreint"
```

## Parcours de vérification

1. Lancez migrations, puis `create_first_admin`; connectez-vous à `POST /api/auth/login` avec les identifiants affichés.
2. Déposez un JPEG/PNG/TIFF/PDF avec la commande ci-dessus. La réponse contient sa `reference` et le fichier chiffré est dans `backend/media/documents`.
3. Vérifiez `GET /api/search?q=<reference>` puis ouvrez `GET /api/documents/<reference>` avec le Bearer token : le navigateur reçoit le bon type de contenu. Utilisez `/export` pour le téléchargement.
4. Vérifiez `GET /api/audit/export?scope=cabinet` : l'upload, la recherche et la consultation y sont tracés.

Tests : `pytest`. Schéma : `python manage.py spectacular --file openapi.yaml`; Swagger est disponible sur `/api/docs/`.

## Conformité des routes

| Domaine | Routes |
|---|---|
| Auth | `/auth/login`, `/auth/mfa/verify`, invitations et activation OTP, réinitialisation, Google pour comptes existants |
| Documents | `/documents`, `/documents/upload`, contrôle qualité, versions, détail, export, validate, archive, favorite, queue, `/search` |
| Gestion | `/dossiers`, `/users`, `/permissions`, `/access-requests` |
| Administration | `/settings`, `/backups/restore-test`, `/audit/export` |
| Divers | `/me`, `/notifications/read`, `/tasks`, `/context/filiale` |

### Sauvegarde réelle

L'endpoint `POST /api/backups/run` réalise désormais une vraie copie des fichiers chiffrés dans `BACKUP_LOCAL_ROOT` et produit un `manifest.json` contenant les références, versions, SHA-256 et identifiants de clés nécessaires au contrôle. Si un stockage S3/compatible S3 est configuré, le même jeu de fichiers est transféré vers le bucket hors site. Les secrets de chiffrement ne sont jamais copiés dans le backup : leurs identifiants sont consignés et les clés doivent être conservées séparément dans le gestionnaire de secrets du cabinet.

Variables S3 : `BACKUP_CLOUD_ENABLED`, `BACKUP_CLOUD_BUCKET`, `BACKUP_CLOUD_PREFIX`, `BACKUP_CLOUD_REGION`, `BACKUP_CLOUD_ENDPOINT`, `BACKUP_CLOUD_ACCESS_KEY`, `BACKUP_CLOUD_SECRET_KEY`. Ne passez `BACKUP_CLOUD_ENABLED=true` qu'après avoir configuré et testé le bucket.

La destruction définitive suit maintenant `corbeille → demande → autorisation → destruction`. L'action `destroy` supprime réellement le binaire chiffré du stockage tout en conservant le registre documentaire, le hash d'origine et la trace d'audit. Un `legal_hold` bloque toujours la destruction.
