# Correctifs issus de l'audit complet — à lire avant la mise en service

Ce document résume ce qui a changé, ce que les utilisateurs vont remarquer, et
ce qui reste ouvert. Chaque correctif est couvert par un test dans
`backend/tests/test_correctifs_audit.py`.

---

## 1. Failles de confidentialité corrigées

### L'export de dossier livrait les pièces « Très confidentiel »
`backend/dossiers/views.py` — `DossierExportView`

L'export ne vérifiait que l'accès **au dossier**, puis écrivait **toutes** ses
pièces dans le ZIP. Un collaborateur simplement affecté téléchargeait donc, en
un clic depuis son propre portail, le contenu en clair d'actes que la route de
détail lui refuse (403).

L'export est désormais borné à ce que le demandeur a le droit de voir. Il reste
sincère : le bordereau indique **combien** de pièces ont été écartées, sans
jamais les nommer.

### La file de numérisation livrait le texte OCR des pièces interdites
`backend/documents/views.py` — `QueueView`

Le rôle ouvre la file ; il n'ouvre pas les pièces. La file est filtrée, et le
compteur du tableau de bord suit exactement la même règle.

### On pouvait déposer dans un dossier auquel on n'a pas accès
`backend/documents/views.py` — `upload_document`

Rien ne vérifiait l'accès au dossier visé : un clerc pouvait glisser une pièce
dans une affaire confidentielle qu'il n'a pas le droit d'ouvrir, et fausser au
passage son inventaire.

### Abaisser un dossier déclassifiait ses pièces en silence
`backend/dossiers/views.py` — `DossierDetailView.patch`

Le niveau du dossier est un **plancher**, pas une valeur recopiée. Relever le
dossier relève les pièces restées en dessous ; l'abaisser ne touche plus à
rien, sauf demande explicite (`appliquerAuxPieces: true`), tracée au journal
avec le nombre de pièces concernées.

---

## 2. Authentification

### La connexion Google contournait le second facteur
`backend/accounts/views.py` — `GoogleOAuthConsumeView`

Google atteste l'adresse e-mail, pas la possession du second facteur. Un
notaire se connectant par Google obtenait une session complète de 8 h sans
code. La règle de risque (`exige_second_facteur`) vaut maintenant pour **tous**
les chemins d'authentification, et l'écran de saisie du code est le même.

### La politique de mot de passe ne s'appliquait pas
`backend/accounts/serializers.py`

`AUTH_PASSWORD_VALIDATORS` n'était jamais appelé : « Password1 », « Azerty12 »
ou « Abcd1234 » étaient acceptés. Les validateurs sont invoqués, le minimum
passe de 8 à **12 caractères**, et les écrans affichent la même règle.

### La limitation de débit se contournait avec un en-tête
`backend/ged_backend/reseau.py` (nouveau), `accounts/throttles.py`

nginx concatène l'en-tête `X-Forwarded-For` envoyé par le client
(`$proxy_add_x_forwarded_for` = « ce que dit le navigateur, puis l'adresse
réelle ») : il suffisait de le faire varier pour repartir à zéro — mesuré,
26 tentatives de connexion sans un seul 429.

`X-Forwarded-For` n'est désormais **jamais** lu. L'adresse vient de
`X-Real-IP`, que nginx pose avec `proxy_set_header` — lequel **remplace** la
valeur reçue et n'est donc pas falsifiable. Réglable par
`TRUSTED_CLIENT_IP_HEADER` (vide = se rabattre sur `REMOTE_ADDR`, à utiliser si
l'application tourne sans reverse proxy devant elle).

### L'adresse du journal d'audit était dictée par la personne tracée
`backend/audit/services.py`

Même cause, conséquence plus grave : l'adresse consignée venait elle aussi de
`X-Forwarded-For`. Un journal notarial dont l'adresse est choisie par celui
qu'il trace ne prouve rien. Il passe par le même module.

### L'étude pouvait se retrouver sans notaire actif
`backend/accounts/views.py` — `UserCreateView.patch`

Un notaire pouvait suspendre son propre accès, ou celui du dernier notaire.
Les deux sont refusés (409).

---

## 3. Exploitation — deux services qui ne faisaient pas leur travail

### ⚠️ La sauvegarde automatique n'avait jamais lieu
`backend/deploy/entrypoint.sh`

`entrypoint.sh` **ignorait ses arguments** et lançait gunicorn quoi qu'il
arrive. Le conteneur `backup` faisait donc tourner un second serveur web au
lieu de la boucle de sauvegarde — alors que l'écran de supervision laissait
croire le contraire.

Le script passe désormais le relais à la commande fournie. Les services annexes
portent `RUN_MIGRATIONS=false` : trois conteneurs qui lancent `migrate` au même
instant, ce sont trois transactions concurrentes sur la même table.

**À vérifier au premier démarrage :**

```bash
docker compose logs backup --tail 20
```

Vous devez y voir la sauvegarde s'exécuter, pas des lignes gunicorn.

### Les rappels d'échéance n'étaient jamais envoyés
`backend/notifications/management/commands/envoyer_rappels.py`

Le formulaire collectait un champ *Rappel*, le modèle le stockait, l'API le
renvoyait — et rien ne l'envoyait. C'était la seule fonctionnalité simulée de
l'application. La commande émet les rappels **et** signale les échéances
dépassées, à l'intéressé comme au donneur d'ordre, une fois et une seule.

---

## 4. Performance

- **L'OCR ne tourne plus dans la requête de dépôt.** Rasteriser 50 pages à
  300 dpi occupait un fil d'exécution plusieurs minutes ; gunicorn n'en offre
  que six et nginx coupe à 180 s. La pièce reste « OCR en attente » et la
  commande `traiter_ocr` s'en charge. Effet de bord bienvenu : le texte des
  **nouvelles versions** est enfin extrait, alors qu'il ne l'était jamais.
- **Fin du N+1 sur les listes** (`documents/serializers.py` — `contexte_liste`) :
  le coût d'une liste ne dépend plus du nombre de documents. Mesuré avant
  correction : 82 requêtes SQL pour 80 pièces.
- **Index ajoutés** sur `statut`, l'ordre par défaut `-created_at`, le trio
  `is_archived/is_current/trashed_at`, `dossier+is_current`, le journal d'audit
  (date, action, acteur, cible), la cloche de notifications et les tâches.

---

## 5. Métier

- **Le journal d'audit est enfin exploitable** : `GET /api/audit` renvoie
  `{ total, returned, entries }` avec l'ancienne et la nouvelle valeur, le
  motif et l'empreinte de chaînage ; il se filtre par action, cible, acteur,
  résultat et période. L'export CSV porte deux colonnes de plus : `details` et
  `entry_hash`. L'écran affiche la transition « ancien » → « nouveau ».
- **Le contrôle qualité n'arrive plus pré-coché.** Sept cases cochées d'avance,
  ce n'était pas un contrôle.
- **La recherche ignore les versions remplacées** ; l'historique reste
  accessible par `/api/documents/<ref>/versions`.
- **Révocation d'habilitation toujours ciblée** : un `DELETE /api/permissions`
  au corps vide effaçait **toutes** les habilitations de l'étude, sans trace
  exploitable. Il est refusé (400) et chaque révocation est journalisée en
  détail, avec notification au bénéficiaire.
- **Mettre en favori une pièce interdite** ne confirme plus son existence.
- **Un dossier contenant une pièce détruite** s'exporte au lieu de renvoyer 500.
- **Les toasts sont annoncés aux lecteurs d'écran** (`role="status"`,
  `aria-live="polite"`).

---

## 6. Ce que les utilisateurs vont remarquer

| Changement | Conséquence pratique |
|---|---|
| Dépôt conditionné à l'accès au dossier | Le notaire doit **affecter** clercs et collaborateurs aux affaires qu'ils traitent. Sans affectation, message explicite : « …ou vous n'y êtes pas affecté. » |
| File de numérisation filtrée | Un clerc ne voit plus que les affaires qui le concernent — et ce qu'il a lui-même déposé. |
| OCR différé | Une pièce fraîchement déposée n'est pas immédiatement trouvable par son contenu (jusqu'à `WORKER_INTERVAL_SECONDS`). Son statut affiche « En attente ». Le bouton « Lancer l'OCR » reste disponible. |
| Mot de passe : 12 caractères | Les comptes existants ne sont pas affectés ; la règle s'applique à la création et à la réinitialisation. |
| Connexion Google du notaire | Demande désormais un code par e-mail. **Le SMTP doit fonctionner**, sinon plus aucune connexion notaire n'aboutit. |

---

## 7. Nouvelles variables d'environnement

Voir `backend/.env.prod.example` : `NUM_PROXIES`,
`TRUSTED_CLIENT_IP_HEADER`, `WORKER_INTERVAL_SECONDS`, `OCR_BATCH`, `RAPPELS_EMAIL`.

Nouveau service `travaux` dans `backend/docker-compose.yml` (OCR + rappels).

---

## 8. Ce qui reste ouvert

**Prérequis d'exploitation, non couverts par le code :**

1. **TLS** devant nginx. `SECURE_SSL_REDIRECT=true` en production.
2. **SMTP réel** — obligatoire : sans lui, ni code de second facteur, ni
   invitation. La configuration est vérifiée au démarrage (échec rapide).
3. **Séquestration de la clé `DOCUMENT_ENCRYPTION_KEY` hors du serveur.**
   L'instantané de sauvegarde est chiffré avec elle : serveur et clé perdus
   ensemble, les archives sont irrécupérables.
4. **Bucket S3** pour la copie hors site, si souhaité.

**Améliorations non faites, par choix :**

- **Index texte (`pg_trgm` / GIN) sur `extracted_text`.** Mesuré : la recherche
  plein texte est un balayage séquentiel, 630 ms pour 5 000 pièces, linéaire
  (≈ 6 s à 50 000). La correction demande `CREATE EXTENSION pg_trgm`, qui exige
  des droits que tous les hébergeurs n'accordent pas — une migration qui échoue
  bloquerait le déploiement. À faire dans un second temps, une fois
  l'hébergement connu. **Elasticsearch ne se justifie pas** : PostgreSQL suffit
  largement à la volumétrie d'une étude.
- **Signature électronique** : absente du modèle comme de l'interface. Le cycle
  de vie va de « validé » à « archivé » sans étape « signé ». Rien ne la
  revendique à l'écran.
- **Durée de conservation (DUA) et sort final** : la destruction reste
  entièrement manuelle, encadrée par le workflow en trois temps et le gel
  juridique.
- **Comptes partagés** : signalés sur le tableau de bord du notaire (connexions
  depuis plusieurs adresses IP), jamais bloqués techniquement.
- **Verrouillage d'objet (WORM)** sur la copie hors site.
- **Jeton de session** : 8 h, en `localStorage`, sans expiration d'inactivité.

---

## 9. Vérification

```bash
cd backend
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest -q
```

Au déploiement, `entrypoint.sh` applique les migrations (nouveaux index et
champs de suivi des rappels) avant de démarrer.

---

## Chargement en skeleton — fin du flash de données fictives

Les 3 portails tournent en mode réel (`useMock:false`), mais plusieurs pages
gardaient des fiches d'exemple codées en dur dans le HTML (documents
« Villa Cocody », « Succession N'Guessan »… sur le tableau de bord, ligne
d'audit « Me Konan »…) ou un simple texte « Chargement… ». Le temps que
l'appel API réponde, ce contenu figé s'affichait avant d'être brutalement
remplacé par les vraies données.

`assets/responsive.css` (partagé par les 3 portails) reçoit un système de
skeleton (shimmer animé, respecte `prefers-reduced-motion`). Chaque
`assets/app.js` de rôle exécute désormais `renderInitialSkeletons()` en tout
premier, avant le moindre appel API : elle remplace immédiatement la grille
de documents, les tableaux (dossiers, utilisateurs, permissions, file de
numérisation, historique de sauvegarde), les listes (notifications,
recherches sauvegardées, alertes) et le titre de la fiche document par un
skeleton. Les fonctions `renderXxx()` déjà existantes écrasent ensuite ce
skeleton dès que la réponse réelle arrive — aucune autre logique de
chargement n'a été modifiée. En mode démo (`useMock:true`), rien ne change :
le contenu d'exemple reste affiché tel quel.

**Reste ouvert, hors périmètre de ce correctif** : `clients.html` et
`taches.html` (sur les 3 rôles) ne sont reliés à aucun appel API dans
`app.js` — ils affichent « Chargement… » indéfiniment, sans jamais charger de
données. Ce n'est pas un problème d'affichage mais un manque de câblage
fonctionnel côté client (aucune fonction `loadClients`/`loadTasks` n'existe).
