# Déploiement GED — checklist de mise en production

## 1. Prérequis
- Docker Desktop / Docker Engine + Compose.
- Un nom de domaine pointant vers le serveur.
- Un SMTP réel pour MFA/invitations.
- Une clé de chiffrement documentaire conservée hors du serveur applicatif.
- Optionnel mais recommandé : un bucket S3/compatible S3 pour la copie hors site.
- TLS via le reverse-proxy/hébergeur (Nginx est prêt à être placé derrière un certificat).

## 2. Préparer les secrets
```bash
cp .env.prod.example .env.prod
```
Remplacer tous les `GENERATE_*`, le domaine, SMTP et Google OAuth.

Générer une clé documentaire :
```bash
python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

## 3. Premier démarrage
Docker Compose ne lit pas automatiquement `.env.prod` (il ne connaît que le
fichier nommé exactement `.env`) : il faut donc préciser `--env-file .env.prod`
sur **chaque** commande `docker compose`, sinon des variables comme
`POSTGRES_PASSWORD` ressortent vides et le démarrage échoue.
```bash
docker compose --env-file .env.prod build
docker compose --env-file .env.prod up -d
```
Pour créer le premier compte Notaire une seule fois : mettre `CREATE_INITIAL_ADMIN=true`, renseigner `INITIAL_ADMIN_EMAIL`, lancer `docker compose --env-file .env.prod up -d --force-recreate web`, récupérer le mot de passe dans les logs, puis remettre `CREATE_INITIAL_ADMIN=false`.

Tant qu'aucun certificat TLS n'est en place devant Nginx (voir section 7),
laisser `SECURE_SSL_REDIRECT=false` dans `.env.prod` — sinon Django redirige
toute requête vers du HTTPS qui n'existe pas encore et rien ne répond.

## 4. Vérifications obligatoires
```bash
docker compose --env-file .env.prod ps
docker compose --env-file .env.prod logs --tail=200 web
curl http://127.0.0.1/api/health
```
Puis dans le navigateur : `/login.html`.

Tester avec un vrai compte : connexion → MFA reçu par email → création d'un dossier → dépôt d'un PDF texte → dépôt d'un PDF scanné → recherche d'un mot contenu uniquement dans le scan → contrôle qualité → téléchargement → corbeille/restauration → sauvegarde → test de restauration.

## 5. OCR
L'image Docker installe Poppler + Tesseract + français/anglais. Les PDF avec couche texte passent par `pypdf`; les PDF scannés passent par rasterisation + Tesseract. La rasterisation est limitée à `OCR_MAX_PAGES` et ne rasterise plus inutilement les pages au-delà de cette limite.

## 6. Sauvegardes
Le service `backup` lance une sauvegarde complète toutes les 6 heures par défaut. Les documents restent chiffrés et la base est exportée avec un manifeste. Pour un PRA réel, activer également `BACKUP_CLOUD_ENABLED=true` et tester une copie vers le fournisseur S3.

## 7. TLS
Le fichier Nginx fourni est en HTTP seul (port 80) pour simplifier le tout
premier démarrage — c'est pourquoi `SECURE_SSL_REDIRECT=false` est nécessaire
tant qu'aucun certificat n'est en place (sinon Django redirige vers un HTTPS
inexistant et le site ne répond plus, y compris `/api/health`).

Avant toute ouverture sur un vrai domaine public :
1. Obtenir un certificat (ex. Let's Encrypt/Certbot) pour le nom de domaine.
2. Ajouter un `server { listen 443 ssl; ... }` dans `deploy/nginx.conf` avec
   les chemins vers le certificat et la clé (ou déléguer la terminaison TLS à
   un reverse-proxy/CDN placé devant Nginx).
3. Repasser `SECURE_SSL_REDIRECT=true` dans `.env.prod` puis redémarrer :
   `docker compose --env-file .env.prod up -d --force-recreate web nginx`.
4. Vérifier que `https://votre-domaine/api/health` répond, puis seulement
   ensuite considérer le déploiement public.

Le backend refuse de démarrer si `SECRET_KEY`, `ALLOWED_HOSTS` ou
`DOCUMENT_ENCRYPTION_KEY` manquent, quel que soit ce réglage.

## 8. Limites à ne pas oublier
- Le chiffrement documentaire ne remplace pas le chiffrement du disque/volume.
- Les clés ne sont jamais incluses dans les sauvegardes : conserver une copie séparée et testée.
- Ne jamais exposer `/media/` directement : les fichiers sont chiffrés mais leur exposition brute reste indésirable. Les téléchargements passent par les endpoints authentifiés.
