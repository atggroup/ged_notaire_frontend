# GED Notaire — Fichier de passation (mise à jour en continu)

> Si l'IA qui travaille sur ce fichier s'arrête en cours de route (tokens
> épuisés), une autre IA doit pouvoir reprendre à partir de ce document
> uniquement, sans relire toute la conversation.

## Contexte projet

- App de GED (gestion électronique de documents) pour un cabinet notarial.
- Stack : Django 5.1+/DRF + PostgreSQL + Docker Compose (services `db`,
  `web`, `nginx`, `backup`) + 3 frontends HTML/JS statiques par rôle
  (`notaire-admin/`, `clerc-principal/`, `collaborateur/`).
- Propriétaire : Eric (dev front-end, projet pour AIS/ATG à Abidjan).
- **Objectif utilisateur : mise en ligne prévue le soir du 10/09/2026.**
- **L'utilisateur a un déploiement Docker Compose FONCTIONNEL sur sa
  machine Windows** (`docker compose --env-file .env.prod up -d`), testé
  en conditions réelles (Postgres réel, SMTP Gmail réel, connexion
  navigateur réussie, action PATCH dossier confirmée par capture d'écran).
- **Dernier zip livré à l'utilisateur : `ged_project_v17_multi_roles.zip`** (note : v13 à v16 ont ajouté d'autres correctifs UI/design par la suite, non documentés en détail dans les entrées numérotées ci-dessus faute de contexte complet à la reprise — voir historique de conversation si besoin)
  dans `/mnt/user-data/outputs/`. **Toujours repackager depuis le dossier
  de travail du sandbox**, pas depuis un zip uploadé plus ancien — c'est
  la version la plus à jour avec tous les correctifs.
- Le code de travail vit dans le sandbox à `/home/claude/ged_project_v10_multi_roles/`
  (copie de travail issue de `/home/claude/ged/ged_project_v9_multi_roles/`,
  lui-même extrait du zip v9 uploadé par l'utilisateur).
- **Le sandbox perd son état à chaque redémarrage complet de session**
  (mais pas entre deux appels d'outils successifs dans la même session) :
  Postgres doit être réinstallé/redémarré, le venv recréé, et surtout
  **tout correctif appliqué au code doit être réintégré depuis ce fichier**
  si le zip re-fourni par l'utilisateur ne les contient pas encore (cela
  s'est produit deux fois de suite : v9 → v9 encore sans les correctifs
  de la session précédente, faute d'avoir été repackagé côté utilisateur
  avant la coupure).

## Bugs déjà trouvés et corrigés (confirmés par tests réels, présents dans v10)

1. **PATCH dossier renvoyait 405** — logique écrite sur `DossierView.patch`
   au lieu de `DossierDetailView.patch`. Corrigé dans
   `backend/dossiers/views.py`. Test : `test_patch_dossier_detail_updates_statut_and_legal_hold`.
   Validé dans le vrai navigateur de l'utilisateur (capture d'écran).

2. **Migration Django manquante** — `documents/models.py` avait des choix
   de statut plus récents que la migration appliquée. Migration :
   `backend/documents/migrations/0007_alter_document_statut.py`. Appliquée
   avec succès sur le vrai Postgres Docker de l'utilisateur.

3. **L'affectation d'un dossier à un clerc/collaborateur ne notifiait
   jamais la personne** — corrigé dans `dossiers/views.py`
   (`DossierAssignmentView.post`, appel à `notify()` quand `created=True`).
   Test : `test_dossier_assignment_notifies_the_assigned_user`.

4. **`GET /dossiers/<ref>` ne renvoyait jamais les clés `documents` et
   `tasks`** — les onglets "Documents" et "Tâches" de la page dossier
   étaient donc toujours vides, quel que soit le contenu réel. Corrigé
   dans `dossier_payload()` (backend/dossiers/views.py) : ajout du
   filtrage de confidentialité via `has_document_access` (les documents
   "Très confidentiel" restent invisibles sans permission explicite,
   même pour un utilisateur assigné au dossier) et des tâches liées
   (filtrées par utilisateur pour un collaborateur, complètes pour
   admin/clerc). Test de régression :
   `test_dossier_detail_includes_filtered_documents_and_tasks`. **Vérifié
   en HTTP réel** : dossier créé, clerc affecté, 2 documents uploadés
   (standard + "Très confidentiel") — l'admin voit les 2, le clerc n'en
   voit qu'1.

5. **Bouton "+ Affecter" un dossier absent du frontend** — `operations.js`
   de `notaire-admin` n'affichait les affectations qu'en lecture seule (le
   endpoint backend `POST/DELETE /dossiers/<ref>/assignments` existait
   déjà mais n'avait aucun bouton pour l'appeler). Réintégré dans
   `notaire-admin/assets/operations.js` : bouton "+ Affecter" (modale
   utilisateur/rôle/échéance, peuplée depuis `GET /users`) + bouton
   "Retirer" par ligne d'affectation (`DELETE .../assignments` avec
   `{id}`). **Testé en HTTP réel** en simulant exactement les appels que
   les boutons envoient : affectation → apparaît dans `GET /dossiers/<ref>`
   → retrait → disparaît.

6. **Boutons sensibles non masqués par rôle** dans `clerc-principal` et
   `collaborateur` (`dossier-detail.html` via `operations.js`) — les
   boutons "Faire avancer le dossier", "+ Ajouter une pièce à la
   checklist" et "Activer/Lever la conservation légale" s'affichaient
   pour tout le monde alors que le backend les réserve respectivement à
   admin+clerc (les deux premiers) et à admin seul (conservation légale).
   Un collaborateur cliquant dessus recevait juste une erreur "Opération
   impossible" sans comprendre pourquoi. Corrigé : les boutons sont
   maintenant générés conditionnellement (`canAdvance`/`canHold` basés
   sur `cfg.role`, qui est fixe par espace — un compte `collaborateur`
   ne peut être connecté que dans l'espace `collaborateur/`), avec un
   message "Aucune action disponible pour votre rôle" si aucun bouton
   n'est affiché. **Vérifié en HTTP réel** : un collaborateur assigné au
   dossier reçoit bien 403 sur `PATCH .../statut` et `PATCH .../legalHold`
   (`clerc-principal/assets/operations.js` et `collaborateur/assets/operations.js`
   sont identiques à `notaire-admin/assets/operations.js` à l'exception
   de ce gating et de l'absence du bouton "+ Affecter", réservé à l'admin).

7. **Espace Collaborateur sans page "Dossiers" (404 sur navigation)** —
   trouvé en test réel dans le vrai navigateur de l'utilisateur (pas
   détecté par l'audit statique de la session précédente, qui ne
   vérifiait que les boutons, pas les liens de menu). `collaborateur/`
   n'avait AUCUN fichier `dossiers.html`, alors que 3 pages
   (`clients.html`, `taches.html`, `dossier-detail.html`) avaient un
   lien de menu "Dossiers" pointant dessus → 404 au clic. Les 7 autres
   pages de l'espace (`index.html`, `documents.html`,
   `document-detail.html`, `notifications.html`, `profil.html`,
   `recherche.html`, `scan.html`) avaient un menu différent, plus court,
   sans lien "Dossiers"/"Clients"/"Tâches" du tout — deux styles de menu
   coexistaient dans le même espace. Corrigé : création de
   `collaborateur/dossiers.html` (calqué sur `clerc-principal/dossiers.html`,
   sans bouton "Nouveau dossier" — la création reste réservée au notaire) et
   **unification du menu sur les 10 pages de l'espace collaborateur**
   (Tableau de bord, Documents, Recherche, Dossiers, Clients, Tâches,
   Scanner — ce dernier avec `data-cap="scan"` puisque `scan:false` pour
   ce rôle). **Testé en HTTP réel** : `GET /dossiers` avec un compte
   collaborateur renvoie bien la liste filtrée à ses dossiers affectés
   (200, contenu correct). Au passage, nettoyé un `href="permissions.html"`
   mort sur le bouton "Demander l'accès" de `document-detail.html`
   (page inexistante côté collaborateur, mais bouton déjà intercepté par
   JS avant modification — pas un bug fonctionnel, juste un `href` de
   repli à corriger par propreté).

8. **Incohérence de bibliothèque d'icônes sur 9 pages (3 rôles × 3 pages)** —
   `dossier-detail.html`, `taches.html` et `clients.html` dans les 3 espaces
   utilisaient des glyphes Unicode bruts (⌂ ▰ ♙ ▤ ⚙ ⌕ ⇥ ♧ ✓ ⌄ etc.) en guise
   d'icônes de navigation/topbar/hero, alors que le reste de l'app
   (`index.html`, `documents.html`, `recherche.html`, `scan.html`,
   `dossiers.html`, etc.) utilise des icônes SVG au trait cohérentes
   (style Feather/Lucide). Ces 3 pages avaient manifestement été générées
   à partir d'un gabarit plus ancien, jamais mis à niveau. Corrigé (design
   pur, **aucune ligne de backend ni de logique JS touchée**) :
   - Le bloc `<div class="rail">` (logo + navigation + pied) de ces 9
     pages a été remplacé par le bloc SVG canonique de la page
     `index.html` du même rôle, avec le bon lien marqué actif et les
     attributs `data-cap="..."` préservés (ex. Scanner reste masqué pour
     un Collaborateur via `data-cap="scan"`, inchangé).
   - Les icônes du bandeau supérieur (loupe de recherche, cloche de
     notification, chevron du menu profil) ont été remplacées par leurs
     équivalents SVG.
   - Sur `taches.html`/`clients.html`, l'icône "hero" en haut de page
     (✓ / ♙) a été remplacée par la même icône SVG que celle utilisée
     dans le menu pour "Tâches"/"Clients", et le champ de recherche large
     (`search-wide`) a reçu la même icône loupe SVG que le reste de
     l'app.
   **Vérifié** : structure HTML équilibrée sur les 9 fichiers (balises
   `<div>` ouvertes/fermées comptées, 3 balises `<script>` intactes par
   page, fermeture `</html>` correcte), et `data-cap="scan"` confirmé
   toujours présent et correctement positionné sur les pages
   collaborateur concernées. Scan final sur l'ensemble du projet : plus
   aucun glyphe Unicode utilisé comme icône nulle part (les seules
   flèches "→" restantes, dans `recherche.html`/`configuration.html`,
   sont du texte normal — ex. "12/06/2026 → 12/07/2026" — pas des
   icônes, laissées telles quelles).

Suite de tests : **26/26 passent** (`pytest -q`, Postgres réel dans le sandbox) — backend non modifié par ce correctif design, tests non re-relancés pour ce point (aucun fichier backend touché).
9. **Popup navigateur `window.prompt()` pour le code MFA à la connexion** —
   remonté par l'utilisateur comme incohérent visuellement (fenêtre système
   grise "localhost indique", hors de tout contrôle de style, contrairement
   au reste de l'app). Corrigé en réutilisant le composant de saisie de
   code déjà existant pour "mot de passe oublié" et l'inscription (cases à
   6 chiffres avec avance automatique, collage, minuteur de renvoi) :
   nouvelle étape `data-step="login-verify"` ajoutée dans `login.html`
   (juste après l'étape `login`) et nouvel enregistrement `META["login-verify"]`
   dans `assets/auth-flow.js` (sans quoi `goTo()` ignore silencieusement
   l'appel — piège déjà rencontré en construisant le correctif). Le
   `window.prompt()` a été retiré du handler `loginForm.submit` ; le code
   MFA passe désormais par `goTo("login-verify")` + `prepLoginOtp()`, avec
   soumission via `EP.mfaVerify` et renvoi de code via un nouvel appel à
   `EP.login` (pas d'endpoint dédié de renvoi MFA côté backend — le login
   régénère et renvoie un nouveau code à chaque appel, comportement déjà
   utilisé tel quel). **Fichier concerné : uniquement `login.html` et
   `assets/auth-flow.js` à la racine du projet — communs aux 3 espaces,
   backend et pages internes aux rôles non touchés** (diff vérifié contre
   le zip v16 reçu : exactement ces 2 fichiers). **Testé en HTTP réel** :
   cycle complet `POST /auth/login` (renvoie bien `mfaRequired` + `email`)
   → code intercepté → `POST /auth/mfa/verify` → session valide (200,
   token/role/name/email) ; et un code invalide renvoie bien une erreur
   exploitable par `markError()`/toast (`400 {"detail":"Code invalide."}`).

## Audit des ~40 pages HTML (fait cette session)

Script d'audit statique (id/data-attribute/classe référencés dans le JS
chargé par chaque page) sur les 3 frontends. Deux catégories de résultats :
- Les `<a class="nav-i" href="...">` de navigation : pas de faux positif
  réel, ce sont de simples liens, pas besoin de JS.
- 5 boutons `<button class="btn pri ...">` sans `id`/`data-*` détectés
  comme "non câblés" par le script → **tous vérifiés comme de faux
  positifs** : ils sont câblés via `Array.from(document.querySelectorAll(...))
  .find(b => /texte du bouton/i.test(b.textContent))` ou via un sélecteur
  positionnel (`document.querySelector(".btn.pri")`), pas via id/data
  attribute. Concerné : "Ouvrir l'accès" (permissions.html), "Enregistrer"
  (configuration.html), "Lancer la recherche" (recherche.html),
  "Lancer une restauration test" (sauvegarde.html), "Envoyer au contrôle"
  (scan.html). **Aucun bouton réellement mort trouvé.**

## Déploiement Docker réel de l'utilisateur — pièges rencontrés et notes

- **Piège volume Postgres** : changer `POSTGRES_PASSWORD` dans
  `.env.prod` n'a AUCUN effet si le volume Postgres existe déjà. Solution :
  `docker compose --env-file .env.prod down -v` puis `up -d`.
- **Piège ALLOWED_HOSTS** : il faut `ALLOWED_HOSTS=localhost,127.0.0.1`
  (le healthcheck nginx interne tape sur `127.0.0.1`). Cette variable ne
  doit JAMAIS contenir de schéma (`http://`) — contrairement à
  `CSRF_TRUSTED_ORIGINS` et `CORS_ALLOWED_ORIGINS`.
- **Piège docker compose sans `.env`** : préciser `--env-file .env.prod`
  à chaque commande, ou copier `.env.prod` vers `.env`.
- **EMAIL_BACKEND=smtp avec host/user/password vides fait planter le
  démarrage** (`prod.py` refuse volontairement). Pour un test sans SMTP
  réel, repasser sur `django.core.mail.backends.console.EmailBackend`.
- SMTP Gmail réel : fonctionne avec un mot de passe d'application
  (validation en 2 étapes activée requise). **Confirmé en conditions
  réelles.**
- **Piège rencontré cette session (nouveau)** : le `.env` de dev fourni
  dans le zip a `DOCUMENT_ENCRYPTION_KEY=base64-encoded-32-byte-key`
  (placeholder littéral, pas une vraie clé) — tout upload de document
  échoue en 503 "Une clé de chiffrement doit être encodée en base64" tant
  qu'on ne le remplace pas par une vraie clé générée
  (`python -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`).
  **Le `.env` livré dans le zip v10 contient déjà une vraie clé générée**
  pour que le projet fonctionne out-of-the-box en dev/test — à régénérer
  avant toute vraie mise en production (voir rappel plus bas, déjà
  présent).

### Rappel pour l'utilisateur avant la VRAIE mise en ligne (pas juste le test)
- Repasser `CREATE_INITIAL_ADMIN=false` dans `.env.prod`.
- Changer `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS` / `CORS_ALLOWED_ORIGINS`
  pour le vrai nom de domaine.
- Remettre `SECURE_SSL_REDIRECT=true`.
- Régénérer `SECRET_KEY`, `DOCUMENT_ENCRYPTION_KEY` et `POSTGRES_PASSWORD`
  avant la vraie mise en ligne (valeurs de dev/test à ne jamais réutiliser
  en prod publique).

## Tests multi-rôles menés cette session (HTTP réel via requests/curl
contre le sandbox — Postgres réel dans CE sandbox, code identique à
celui livré dans v10)

- [x] Admin crée un dossier, affecte un clerc → clerc voit le dossier et
      reçoit une notification.
- [x] Bouton "+ Affecter" (nouveau) : affectation d'un collaborateur via
      `GET /users` + `POST .../assignments`, puis retrait via
      `DELETE .../assignments`. Flux complet vérifié.
- [x] `GET /dossiers/<ref>` renvoie bien `documents` et `tasks`, filtrés
      par confidentialité/rôle (admin voit tout, clerc/collaborateur
      voient moins).
- [x] Gating des boutons par rôle : `PATCH statut`, `PATCH legalHold` et
      `POST checklist` renvoient bien 403/404 pour un collaborateur — les
      boutons correspondants sont masqués côté frontend dans son espace.
- [x] Upload + OCR réel (tesseract 5.3.4 installé dans le sandbox) : texte
      extrait avec succès sur une image PNG de test.
- [x] Contrôle qualité document (7 vérifications obligatoires) + validation
      notariale (bloquée tant que le contrôle qualité n'est pas passé,
      comme voulu).
- [x] Export de dossier (`GET /dossiers/<ref>/export`) : zip complet
      vérifié (métadonnées, documents, bordereau CSV, historique).
- [x] Sauvegarde : `python manage.py backup_ged` (CLI) + `GET /backups`
      (API) cohérents entre eux.
- [x] Statut des clés de chiffrement (`GET /key-management/status`).
- [x] Pièces physiques : création, sortie (`checkout`), retour (`return`).
- [x] Cycle de demande d'accès complet, testé dans les deux sens :
      collaborateur demande l'accès à un document "Très confidentiel" →
      admin refuse → accès reste bloqué (403) → nouvelle demande → admin
      accepte → accès débloqué (200).
- [x] Legal hold, document confidentiel, accès croisés admin/clerc/collab :
      comportements déjà vérifiés lors de la session précédente, non
      re-testés en détail cette session (code inchangé sur ces points).

## Ce qu'il reste à tester si le temps le permet

- [ ] Upload + OCR depuis l'INTERFACE (navigateur) côté utilisateur — pas
      encore fait dans son vrai déploiement Docker (fait seulement via
      API dans le sandbox, avec succès).
- [ ] Sauvegarde dans le vrai déploiement Docker de l'utilisateur (testée
      avec succès dans le sandbox, jamais refaite côté utilisateur).
- [ ] Rotation de clé de chiffrement, sauvegarde/restauration de secours
      en conditions réelles Docker (testée uniquement par pytest et
      partiellement API dans le sandbox).
- [ ] Vérifier dans le vrai navigateur de l'utilisateur (pas seulement en
      HTTP simulé) : bouton "+ Affecter", onglets Documents/Tâches du
      détail dossier, masquage des boutons par rôle.

## Contraintes de l'environnement sandbox (rappel, confirmées cette session)

- Accès réseau limité (npm, pypi, github, apt ubuntu). Pas de SMTP externe
  testable depuis CE sandbox.
- **Process en arrière-plan ne survivent PAS entre deux appels d'outil
  bash_tool séparés** → toujours démarrer le serveur Django ET faire les
  requêtes de test dans UN SEUL appel bash_tool.
- **Postgres n'est pas démarré automatiquement après un redémarrage de
  session** (`service postgresql start` à refaire — vu cette session :
  pytest échoue avec "Connection refused" sur le port 5432 si oublié).
- `python3 manage.py runserver` bufferise stdout quand redirigé vers un
  fichier — toujours `PYTHONUNBUFFERED=1`.
- Comptes de test dans CE sandbox (différents de ceux de l'utilisateur) :
  - admin : `notaire@cabinet-test.ci` / `TestReel123!`
  - clerc : `clerc.test@cabinet-test.local` / `TestReel123!`
  - collaborateur : `collab.test@cabinet-test.local` / `TestReel123!`
  - DB : `postgresql://ged_user:change-me@127.0.0.1:5432/ged_notaire`

## Prochaine étape immédiate (si tu reprends ce fichier)

1. Relire ce fichier en entier.
2. Vérifier l'état du dossier de travail dans le sandbox ; s'il n'existe
   plus, ré-extraire depuis le dernier zip uploadé par l'utilisateur ou
   depuis `/mnt/user-data/outputs/ged_project_v10_multi_roles.zip`.
   **Si le zip repartait de v9 (sans les correctifs 4/5/6 ci-dessus),
   les réappliquer depuis la description détaillée de ce fichier avant
   de continuer** — c'est arrivé deux fois de suite.
3. Redémarrer Postgres (`service postgresql start`), relancer les
   migrations si besoin, relancer `pytest -q` pour confirmer 26/26.
4. Continuer la checklist "Ce qu'il reste à tester" ci-dessus.
5. À chaque bug trouvé : corriger, ajouter un test de régression,
   relancer pytest, revérifier en HTTP réel, PUIS mettre à jour ce
   fichier AVANT de passer à la suite.
6. Repackager régulièrement vers `/mnt/user-data/outputs/` avec un nom
   versionné croissant (`ged_project_v11_...zip`, etc.), et appeler
   `present_files`.
7. Tenir ce HANDOFF.md à jour à CHAQUE étape franchie.

## Rappel sur le style de réponse attendu par l'utilisateur

L'utilisateur (Eric) veut du travail réel, vérifié en conditions réelles,
pas des affirmations non testées. Toujours dire clairement ce qui a été
testé EN VRAI (HTTP réel, navigateur réel, Docker réel) vs ce qui a
seulement été relu/vérifié statiquement dans le code. Ne jamais affirmer
qu'une fonctionnalité marche sans l'avoir exécutée.

---

## Mise à jour — v18 (13/09/2026, reprise après plusieurs sessions parallèles)

**Attention** : ce fichier n'a pas été tenu à jour en continu entre la v10
et la v17 — plusieurs sessions/instances différentes ont visiblement
travaillé sur le projet en parallèle (le zip "v17_corrigé" reçu de
l'utilisateur contenait des correctifs sur `operations.js`/`operations.css`
que je n'ai pas produits moi-même). Les sections précédentes de ce fichier
restent utiles pour le contexte général (pièges Docker, comptes de test,
contraintes sandbox) mais ne listent pas tous les bugs corrigés entre la
v10 et la v17.

**État confirmé du zip `ged_project_v17_corrige` reçu de l'utilisateur**
(avant les modifications de cette entrée) :
- Popup navigateur `window.prompt()` pour le code MFA → déjà remplacé par
  une interface intégrée (étape `login-verify` dans `login.html` +
  `assets/auth-flow.js`).
- Attribut HTML corrompu `class="class="..."` sur le menu latéral (bug qui
  empêchait le style de s'appliquer sur certains liens de `dossier-detail.html`/
  `taches.html`/`clients.html`/pages collaborateur) → déjà corrigé, aucune
  occurrence restante vérifiée.
- Icônes étoile (favori) et flèche (ouvrir) en caractères Unicode bruts
  (★/☆/↗) dans les cartes de documents → déjà remplacées par du SVG.

**Corrigé dans cette entrée** (seul point qui manquait encore) :
- Espacements trop serrés sur le tableau de bord (cartes stat, "Documents
  récents", carte "Alertes") — augmentés sur les 3 rôles
  (`notaire-admin`, `clerc-principal`, `collaborateur`) selon une logique
  de grille 8px : `.wrap` gap 26→32px, `.col` gap 22→28px, `.aside` gap
  20→24px, `.card` padding 20→24px, `.stats` gap 16→20px, `.stat` padding
  18→22px, `.stat .ic` margin-bottom 14→16px, `.docs` gap 16→20px. Un
  style en ligne `padding:9px 0` qui écrasait le padding CSS de `.lrow`
  sur les lignes de la carte "Alertes" (`index.html` × 3 rôles) a aussi
  été remplacé par `padding:13px 4px`. **Fichiers concernés : uniquement
  `assets/styles.css` et `index.html` dans les 3 dossiers de rôle — aucun
  fichier backend touché.**

**Vérification faite avant repackage** : structure HTML/CSS validée
(accolades CSS et balises `<div>` équilibrées, fichiers se terminant
correctement) sur les fichiers modifiés dans les 3 rôles. Pas de nouveau
test HTTP/pytest nécessaire (aucun fichier backend touché).

**Zip livré : `ged_project_v18_multi_roles.zip`.**

### Recommandation pour la suite

Avant toute nouvelle session de correctifs, faire un point avec
l'utilisateur pour confirmer QUEL zip est réellement déployé en ce moment
sur sa machine (`docker inspect backend-nginx-1 --format
"{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}"`
donne le chemin exact) — plusieurs fois dans cette série de sessions, la
version réellement en ligne s'est avérée être plus ancienne que la
dernière livrée, à cause de redéploiements manqués ou de zips repris
depuis un mauvais dossier.

---

## Mise à jour — v19 (14/09/2026)

Repartie de `ged_project_v18_multi_roles` réellement déployé par l'utilisateur
(le zip renvoyé contenait déjà des ajustements — `operations.js`/`operations.css`
différents des miens — probablement une autre session ; diff vérifié : seul le
backend reste strictement identique modulo `.env.prod`/`docker-compose.yml`
propres à l'utilisateur).

**3 bugs corrigés cette session, sur les 3 rôles (notaire-admin, clerc-principal,
collaborateur) :**

1. **Menu latéral inaccessible en bas de liste** — `.rail-nav` (colonne
   d'icônes) n'avait pas de défilement propre (`overflow:visible` hérité de
   `.rail`), donc sur un écran pas assez haut pour afficher les ~13 icônes du
   menu admin (Tableau de bord → Configuration), les dernières entrées
   sortaient de l'écran sans aucun moyen d'y accéder. Corrigé : `.rail-nav`
   défile désormais verticalement (`overflow-y:auto`, barre de défilement
   masquée visuellement pour rester discrète). **Piège rencontré** : les
   info-bulles (`.nav-i .tip`) étaient en `position:absolute` par rapport à
   chaque icône — une fois `.rail-nav` scrollable, elles auraient été rognées
   horizontalement par le nouveau conteneur de défilement (comportement
   standard des navigateurs : dès qu'un axe passe en `overflow:auto`,
   l'autre axe n'est plus vraiment "visible"). Résolu en passant les
   info-bulles en `position:fixed`, positionnées dynamiquement en JS au
   survol (nouvelle fonction `setupNavTooltips()` dans `app.js`, basée sur
   `getBoundingClientRect()`) — indépendantes de tout conteneur de
   défilement, donc plus jamais rognées, quel que soit l'écran.

2. **Bouton hamburger toujours visible, même sur desktop** — la règle CSS
   `.menu-btn{display:none}` (état par défaut, non-mobile) était neutralisée
   par `.iconbtn{display:flex}`, définie plus bas dans le fichier avec la
   même spécificité CSS (donc la dernière règle du fichier gagnait,
   peu importe la taille d'écran). Corrigé en donnant plus de spécificité
   aux deux règles concurrentes (`.iconbtn.menu-btn{display:none}` par
   défaut, `.iconbtn.menu-btn{display:flex}` sous `@media(max-width:1024px)`)
   — le bouton ne s'affiche maintenant que sur mobile/tablette, comme prévu
   à l'origine.

3. **Pas de bouton "Retour" sur les pages de détail** — `document-detail.html`
   et `dossier-detail.html` (les 2 pages "fiche" atteintes en cliquant sur un
   élément d'une liste) n'avaient qu'un titre statique, aucun moyen de
   revenir à la liste sans ressaisir l'URL ou utiliser le bouton natif du
   navigateur. Ajouté un bouton retour (flèche, cohérent avec le style du
   bouton "Retour" déjà utilisé dans le parcours de connexion) avant le
   titre `<h1>` de ces 2 pages, sur les 3 rôles (6 fichiers). Câblé via un
   attribut `data-go-back` générique + nouvelle fonction `setupBackButtons()`
   dans `app.js` : `history.back()`, avec repli sur `index.html` si la page
   a été ouverte directement (pas d'historique de navigation, ex. lien
   partagé).

**Fichiers concernés** : uniquement `assets/styles.css`, `assets/app.js` et
les 6 fichiers `document-detail.html`/`dossier-detail.html` dans les 3
dossiers de rôle — aucun fichier backend touché. Structure HTML/CSS/JS
validée (accolades CSS et balises équilibrées, syntaxe JS vérifiée avec
`node --check` sur les 3 `app.js`) avant repackage.

**Zip livré : `ged_project_v19_multi_roles.zip`.**

---

## Mise à jour — v22 (15/09/2026, audit UI/UX de cohérence sur v21_audit_fixes)

**Contexte** : reprise du zip `ged_project_v21_audit_fixes.zip` fourni par
l'utilisateur, avec demande explicite d'audit de cohérence UI/UX complet et
de vérification "que tout passe" (statique + tests réels).

**Vérifications effectuées (toutes passées, sauf le point corrigé ci-dessous)** :
- Structure HTML des 42 pages (3 rôles) : balises `<div>`/`<script>`
  équilibrées, fermeture `</html>` correcte sur tous les fichiers — **OK**.
- Aucun glyphe Unicode résiduel utilisé comme icône (seules des flèches "→"
  dans du texte normal, déjà identifiées lors d'une session précédente) — **OK**.
- Accolades CSS équilibrées sur toutes les feuilles de style — **OK**.
- Syntaxe JS (`node --check`) sur tous les `.js` frontend — **OK**.
- Liens internes (`href="*.html"`) : aucun lien mort vers une page
  inexistante dans les 3 espaces — **OK**.
- Palettes de couleurs distinctes par rôle (orange notaire-admin, bleu
  clerc-principal, vert collaborateur) confirmées comme un choix de design
  system intentionnel (différenciation visuelle des rôles), pas une
  incohérence — **OK, pas un bug**.
- `config.js` des 3 rôles : capacités (`capabilities`) cohérentes avec le
  gating de rôle documenté (admin a tout sauf `requestAccess`, clerc a
  `requestAccess`+`audit` sans `users`/`permissions`/`backup`/`config`,
  collaborateur a le minimum sans `scan`/`audit`) — **OK**.
- Backend : migrations appliquées sans erreur sur Postgres réel (sandbox),
  **suite de tests `pytest -q` : 26/26 passent**. Cycle de connexion réel
  testé en HTTP (`POST /auth/login` → `mfaRequired:true` avec un compte
  admin créé via `create_first_admin` ; endpoint protégé `GET /dossiers`
  renvoie bien 401 sans token) — **OK**.

**Bug de cohérence UI/UX trouvé et corrigé** :
- **Menu de navigation (rail-nav) incomplet sur `scan.html`** dans
  `notaire-admin` et `clerc-principal` : il manquait les liens "Clients" et
  "Tâches" (présents sur toutes les autres pages du même rôle, et déjà
  présents sur `collaborateur/scan.html`, qui avait été corrigé lors d'une
  session antérieure — cf. entrée "Incohérence de bibliothèque d'icônes").
  Un utilisateur naviguant vers le Scanner perdait donc temporairement
  l'accès direct à ces deux sections via le rail latéral. Corrigé en
  remplaçant le bloc `rail-nav` de `scan.html` par celui, complet, de
  `index.html` du même rôle (en déplaçant la classe `active` de
  `index.html` vers `scan.html`). **Fichiers concernés : uniquement
  `notaire-admin/scan.html` et `clerc-principal/scan.html` — aucun fichier
  backend touché.** Vérifié après correction : structure `<div>` toujours
  équilibrée sur les 2 fichiers, et les 3 rôles n'ont plus chacun qu'**une
  seule signature de menu** (tous liens identiques sur toutes leurs pages,
  seul l'attribut `active` change) — confirmé par script de comparaison
  automatique des blocs `rail-nav` sur les 42 pages.

**Zip livré : `ged_project_v22_ui_audit.zip`.**

---

## Mise à jour — v23 (15/09/2026, ajout des fonctionnalités backend sans UI)

**Contexte** : suite à l'audit v22, six fonctionnalités existaient côté backend
(testées, avec endpoints fonctionnels) mais n'avaient aucun point d'entrée
dans le frontend. Cette session les a toutes câblées, avec test HTTP réel
pour chacune (Postgres réel, serveur Django réel dans le sandbox).

1. **Archives physiques** (priorité — cœur de l'objectif du projet : ne
   jamais perdre un original papier). Nouvel onglet "Archives physiques"
   dans la fiche dossier (`operations.js`, 3 rôles) : liste des originaux
   avec localisation (salle/armoire/rayon/boîte/chemise), statut
   (en rayon / sorti par qui), et actions Créer / Sortir / Retourner
   réservées à admin+clerc (`canManagePhysical`), lecture seule pour
   collaborateur — conforme au gating serveur
   (`PhysicalArchiveView` : `{"admin","clerc"}`). **Testé en HTTP réel** :
   cycle complet créer → apparition dans `GET /dossiers/<ref>` → sortie →
   retour. Un collaborateur non affecté au dossier reçoit 404 sur ces
   mêmes endpoints (accès refusé proprement).

2. **OCR + Contrôle qualité**. Boutons "Lancer l'OCR" et "Contrôle
   qualité" ajoutés dans `document-detail.html` de **notaire-admin ET
   clerc-principal** (ce dernier n'avait aucun accès à ces fonctions
   bien que le backend les autorise pour son rôle — le fichier ne
   reprenait que le gabarit minimal du collaborateur). Modale de
   contrôle qualité avec les 7 cases obligatoires
   (`complete/ordered/legible/noMissingPage/noDuplicate/orientationCorrect/dossierCorrect`)
   + notes. **Testé en HTTP réel** sur un document uploadé dans la
   session : l'OCR extrait le texte réel de l'image de test, le
   contrôle qualité fait passer le document de `à_indexer` à
   `en_validation`.

3. **Rotation des clés de chiffrement**. `configuration.html` /
   `app.js` (notaire-admin) : liste des clés connues avec boutons
   Activer/Retirer par clé, formulaire d'import de paquet de
   récupération (fichier + phrase secrète). Au passage, remplacé les
   `prompt()`/`alert()` natifs du navigateur (générer une clé, exporter
   la sauvegarde) par les modales déjà utilisées partout ailleurs dans
   l'app — même incohérence UI que le popup MFA corrigé lors d'une
   session antérieure. **Testé en HTTP réel** : génération (avertit
   bien qu'il faut ajouter la clé au secret-store avant activation),
   activation rejetée tant que la clé n'y est pas (409, conforme),
   export + import d'un paquet de récupération (succès avec la bonne
   phrase secrète, échec propre avec une mauvaise phrase), retrait
   bloqué sur la clé active (409, conforme).

4. **Recherches sauvegardées**. Bouton "Sauvegarder cette recherche" +
   liste des recherches sauvegardées (Lancer / Supprimer) ajoutés dans
   `recherche.html` des 3 rôles, câblés dans les 3 `app.js`
   respectifs (la fonction de recherche de notaire-admin a été
   refactorée en `runSearch()` nommée, sur le modèle de clerc/
   collaborateur, pour pouvoir être réutilisée par "Lancer" une
   recherche sauvegardée). **Testé en HTTP réel** : créer → lister →
   supprimer une recherche sauvegardée.

5. **Pré-vérification du code d'invitation**. À l'étape 2 de
   l'inscription (`login.html` + `assets/auth-flow.js`, commun aux 3
   rôles), le code d'invitation est maintenant vérifié à la volée
   (au blur du champ, ou 600ms après la dernière frappe) via
   `POST /auth/register/check-invite`, avec un message inline
   (valide / invalide) sous le champ — au lieu de ne découvrir un code
   invalide qu'après avoir rempli le mot de passe à l'étape suivante.
   Nouvel endpoint `checkInvite` ajouté à `assets/auth-config.js`.
   **Testé en HTTP réel** : code+email valides → `{valid:true,
   role:...}` ; mauvais email ou code inexistant → `{valid:false}`.

**Non traité dans cette session** : rien — les 6 points identifiés lors
de l'audit v22 sont tous couverts.

**Vérifications avant repackage** : structure HTML (balises `<div>`
équilibrées, fermeture `</html>`) et CSS (accolades) sur l'ensemble du
projet — 0 anomalie. Syntaxe JS (`node --check`) sur tous les `.js`
frontend — 0 erreur. **`pytest -q` : 26/26** (aucun fichier backend
modifié par cette session, uniquement du câblage frontend vers des
endpoints déjà existants et déjà testés côté serveur).

**Zip livré : `ged_project_v23_features.zip`.**

### Rappel sécurité (à ne pas oublier avant une vraie mise en ligne)

L'utilisateur a collé son `.env.prod` complet en clair dans la conversation
à un moment de cette session (mot de passe Postgres, clé de chiffrement
documents, mot de passe d'application Gmail, secret OAuth Google). Il a été
prévenu sur le moment de régénérer ces valeurs avant toute vraie mise en
ligne publique — **ne jamais réutiliser ces valeurs telles quelles sur le
domaine final**, et éviter de recoller ce fichier en clair dans une future
conversation.
