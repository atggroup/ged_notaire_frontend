# Mise en production de la GED — guide pas à pas

Ce guide suppose **aucune connaissance préalable**. Suivez les étapes dans
l'ordre ; chacune se termine par une vérification. En cas de doute, arrêtez-vous
et faites vérifier par un prestataire informatique : il s'agit d'actes notariés.

**Protections intégrées** : l'application **refuse de démarrer** sans HTTPS,
avec un domaine générique ou temporaire (tunnel `trycloudflare`), ou s'il reste
une valeur `A_REMPLACER` dans la configuration. Si elle refuse, le message
indique quoi corriger.

---

## 0. Ce qu'il vous faut

| Élément | Recommandation | Coût indicatif |
|---|---|---|
| Serveur | VPS Linux **Ubuntu 24.04**, **8 Go de RAM** (l'antivirus en utilise ~1,5 Go), 2–4 processeurs, 80 Go SSD **chiffré** (OVHcloud, Hetzner, Scaleway, Contabo…) | 15–35 €/mois |
| Surveillance externe | **Healthchecks.io** (gratuit) + **UptimeRobot** (gratuit) | 0 € |
| Nom de domaine | ex. `ged.mon-etude.ci` (sous-domaine du site de l'étude) | 10–30 €/an |
| Stockage hors site | **Backblaze B2** avec *Object Lock* (anti-suppression) | ~0,006 $/Go/mois |
| Messagerie d'envoi | Idéalement un service professionnel (Brevo, Mailjet, Microsoft 365) plutôt qu'un Gmail personnel | 0–10 €/mois |

> Hébergement de données notariales : vérifiez auprès de la Chambre des
> notaires et de l'ARTCI les exigences de localisation et de sécurité des
> données (voir `CONFORMITE_RGPD_ARTCI.md`).

## 1. Préparer le serveur (une fois)

Connecté au serveur en SSH (le prestataire vous fournit l'accès) :

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 ufw fail2ban unattended-upgrades
sudo systemctl enable --now docker fail2ban
sudo dpkg-reconfigure -plow unattended-upgrades   # mises à jour de sécurité automatiques
# Pare-feu : SSH + HTTP (certificat) + HTTPS, rien d'autre.
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443
sudo ufw enable
```

Recommandé : connexion SSH par clé uniquement (`PasswordAuthentication no`
dans `/etc/ssh/sshd_config`), jamais en `root`.

**Vérification** : `sudo ufw status` affiche uniquement 22, 80 et 443.

## 2. Nom de domaine

Chez le registraire du domaine, créez un enregistrement **A** :
`ged.mon-etude.ci` → adresse IP du serveur.

**Vérification** (depuis votre PC) : `ping ged.mon-etude.ci` répond avec
l'adresse du serveur (la propagation peut prendre jusqu'à quelques heures).

## 3. Stockage hors site (Backblaze B2)

1. Créez un compte sur backblaze.com → **B2 Cloud Storage**.
2. **Create a Bucket** : nom unique (ex. `ged-mon-etude-sauvegardes`), *Private*,
   **Object Lock : Enable**, rétention par défaut **Governance, 90 jours**.
3. **Application Keys** → *Add a New Application Key* limitée à ce bucket.
   Notez `keyID`, `applicationKey` et l'**endpoint** (ex.
   `https://s3.eu-central-003.backblazeb2.com`, région `eu-central-003`).

Ces valeurs vont dans `BACKUP_CLOUD_*` (étape 5).

## 4. Renouveler les secrets (ils ont circulé avec le projet)

Le fichier `backend/.env.prod.nouveau` a été préparé avec un **nouveau**
`SECRET_KEY`, un **nouveau** mot de passe PostgreSQL et une **nouvelle** clé de
chiffrement (l'ancienne y est conservée sous l'identifiant `legacy`, le temps du
rechiffrement). Pour le régénérer : `python manage.py preparer_secrets_production --ecraser`.

À faire **chez les fournisseurs** (les anciennes valeurs doivent être considérées comme exposées) :

- **Gmail** (compte Google → Sécurité → *Mots de passe d'application*) :
  révoquez l'ancien, créez-en un nouveau → `EMAIL_HOST_PASSWORD`.
- **Google Cloud Console** → *API et services* → *Identifiants* → client OAuth :
  réinitialisez le secret → `GOOGLE_OAUTH_CLIENT_SECRET` ; ajoutez l'URI de
  redirection `https://ged.mon-etude.ci/auth/oauth/google/callback`.

## 5. Compléter la configuration

Copiez le projet sur le serveur **sans** `backend/.env` ni `backend/.env.prod`
(ils contiennent les anciens secrets). Placez `backend/.env.prod.nouveau` sur
le serveur sous le nom `backend/.env.prod`, puis remplacez chaque `A_REMPLACER` :

```
GED_DOMAIN=ged.mon-etude.ci
GED_ACME_EMAIL=contact@mon-etude.ci
ALLOWED_HOSTS=ged.mon-etude.ci,localhost
CSRF_TRUSTED_ORIGINS=https://ged.mon-etude.ci
GOOGLE_OAUTH_REDIRECT_URI=https://ged.mon-etude.ci/auth/oauth/google/callback
BACKUP_CLOUD_BUCKET=ged-mon-etude-sauvegardes
BACKUP_CLOUD_REGION=eu-central-003
BACKUP_CLOUD_ENDPOINT=https://s3.eu-central-003.backblazeb2.com
BACKUP_CLOUD_ACCESS_KEY=<keyID>
BACKUP_CLOUD_SECRET_KEY=<applicationKey>
```

```bash
chmod 600 backend/.env.prod
grep -c A_REMPLACER backend/.env.prod    # doit afficher 0
```

## 6. Valider sur PostgreSQL (avant toute donnée réelle)

```bash
cd backend
sh deploy/valider_postgres.sh
```

**Vérification** : la dernière ligne affiche `VALIDATION POSTGRESQL RÉUSSIE`.
Sinon : **ne pas déployer**, transmettre la sortie complète.

## 7. Démarrer

```bash
docker compose --env-file .env.prod up -d --build
docker compose ps          # tous les services « running » / « healthy »
```

**Vérification** : `https://ged.mon-etude.ci` affiche la page de connexion avec
le cadenas du navigateur, et `http://ged.mon-etude.ci` redirige vers HTTPS.

## 8. Reprendre les données de l'installation actuelle

Sur la **machine actuelle** (Windows), avec **cette nouvelle version du code**
(elle seule exporte le journal d'audit à l'identique), appliquez les migrations
puis produisez une sauvegarde. `GED_ALLOW_INSECURE_HTTP=true` n'est utilisé que
pour ces deux commandes ponctuelles, sur cette machine locale :

```bash
docker compose --env-file .env.prod build web
docker compose --env-file .env.prod run --rm -e GED_ALLOW_INSECURE_HTTP=true web python manage.py migrate
docker compose --env-file .env.prod run --rm -e GED_ALLOW_INSECURE_HTTP=true -e RUN_MIGRATIONS=false web python manage.py backup_ged
```

Copiez ensuite le répertoire obtenu (dans le volume `backup_data`,
`backup_store/<horodatage>/`) sur le serveur, par exemple dans `backend/restauration/`.
Il est entièrement chiffré ; transférez-le tout de même par un canal sûr (SFTP).

Sur le **serveur** (base neuve, déjà migrée par le démarrage) :

```bash
docker compose --env-file .env.prod run --rm -v "$PWD/restauration:/restauration:ro" web \
  python manage.py restaurer_sauvegarde --dossier /restauration/<horodatage>
```

La commande vérifie tout avant d'écrire, refuse une base non vide, puis
contrôle chaque pièce et la chaîne d'audit.

## 9. Rechiffrer les pièces avec la nouvelle clé

```bash
docker compose --env-file .env.prod exec web python manage.py rechiffrer_documents --simulation
docker compose --env-file .env.prod exec web python manage.py rechiffrer_documents
```

**Vérification** : `0 anomalie(s) ; 0 restante(s)`. Conservez ensuite l'ancienne
clé **uniquement** dans le paquet de récupération (étape 10), le temps que les
anciennes sauvegardes expirent : retirez `legacy` de `DOCUMENT_ENCRYPTION_KEYS`
après 12 mois (durée de rétention mensuelle).

## 10. Séquestrer les clés hors du serveur — indispensable

Sans les clés, **aucune** sauvegarde n'est lisible. Dans la GED :
**Configuration → Clés de chiffrement → Exporter le paquet de récupération**,
avec une phrase secrète longue. Conservez le fichier obtenu **hors du serveur**
(deux clés USB chiffrées, dont une au coffre de l'étude) et la phrase secrète
séparément (sous pli scellé, par exemple).

**Vérification** : écran **Sauvegarde** → « Clés sauvegardées hors du serveur :
Oui ». Tant que ce n'est pas le cas, une alerte critique est envoyée chaque jour.

## 11. Première sauvegarde et exercice de restauration

Écran **Sauvegarde · Supervision** → « Exécuter » sur *Sauvegarde complète
chiffrée*, puis sur *Exercice PRA*.

**Vérification** : les deux sont « Réussi », *Cloud : success* apparaît dans le
message de sauvegarde, et les fichiers sont visibles dans le bucket Backblaze.

## 11 bis. Sécuriser le compte du notaire et brancher la surveillance

**Application d'authentification (obligatoire pour chaque notaire)** : GED →
**Profil → Application d'authentification → Activer**, puis suivez les trois
étapes (clé à saisir dans Google/Microsoft Authenticator, code de contrôle,
codes de secours à imprimer et ranger au coffre). Dès lors, une messagerie
compromise ne suffit plus à prendre le compte.

**Surveillance externe** — si le serveur tombe, l'application ne peut plus
prévenir personne ; ces services le feront :

1. **Healthchecks.io** : créez un « check » (période 5 min, tolérance 10 min),
   copiez son adresse de ping dans `.env.prod` :
   `MONITORING_PING_URL=https://hc-ping.com/<identifiant>`, puis redémarrez.
   L'exécutant l'appelle toutes les 5 minutes ; en cas de silence ou d'échec
   d'un travail (sauvegarde…), vous recevez un e-mail/SMS.
2. **UptimeRobot** : surveillance « Keyword » de `https://ged.mon-etude.ci/api/health`,
   mot-clé `"workers": "ok"` — alerte si le site ou les exécutants sont arrêtés.

**Antivirus** : le service `clamav` démarre avec les autres ; au premier
lancement, le chargement des signatures prend quelques minutes, pendant
lesquelles les dépôts sont refusés (message explicite) — c'est voulu.

**Administration Django** : désactivée et non exposée. Pour une maintenance
exceptionnelle, un prestataire peut l'activer temporairement
(`DJANGO_ADMIN_ENABLED=true`) en y accédant uniquement par tunnel SSH.

## 12. Restaurer après un sinistre (serveur perdu)

1. Nouveau serveur : étapes 1, 2, 5 et 7.
2. Remettre les clés : importer le paquet de récupération
   (Configuration → Clés → *Vérifier un paquet*), recopier la valeur
   `DOCUMENT_ENCRYPTION_KEYS` affichée dans `.env.prod`, redémarrer.
3. Restaurer depuis le stockage hors site :

```bash
docker compose --env-file .env.prod run --rm web python manage.py restaurer_sauvegarde --cloud liste
docker compose --env-file .env.prod run --rm web python manage.py restaurer_sauvegarde --cloud derniere
```

4. Vérifier l'écran Supervision et consulter quelques actes.

Faites cet exercice **complet**, au moins une fois par an, sur un serveur de test.

---

## Contrôles réguliers

| Fréquence | Contrôle |
|---|---|
| Chaque semaine | Écran Supervision : tous les travaux au vert ; aucune alerte de sécurité ouverte ; Healthchecks.io « up » |
| Chaque mois | Un fichier de sauvegarde est bien présent dans Backblaze |
| Chaque année | Restauration complète sur un serveur de test (étape 12), durée mesurée |
| À chaque départ d'un collaborateur | Compte désactivé, sessions révoquées |
