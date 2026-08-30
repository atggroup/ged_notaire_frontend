# GED Notaire — Projet front-end (prêt pour le backend)

## 1. Structure du projet

```
PROJET_GED/
├── index.html                 → redirige vers login.html
├── login.html                 → connexion (point d'entrée unique des 3 espaces)
├── inscription.html           → création de compte (Clerc / Collaborateur)
├── assets/
│   ├── auth.css                → design system des pages Connexion/Inscription (4ᵉ identité visuelle)
│   ├── auth.js                 → validation, appel API, gestion de session
│   └── auth-config.js          → apiBase, endpoints, mapping rôle → dossier
├── 1-notaire-admin/            → espace Notaire · Admin (thème ambre) — 11 pages
├── 2-clerc-principal/          → espace Clerc principal (thème bleu) — 8 pages
└── 3-collaborateur/            → espace Collaborateur (thème vert) — 6 pages
```

Chaque espace rôle possède son propre `assets/config.js` (rôle, thème, capacités)
et partage la même logique `assets/app.js`, dont le comportement s'adapte
entièrement aux `capabilities` déclarées — c'est ce qui fait que la gestion
(actions autorisées, pages accessibles) diffère réellement d'un rôle à l'autre,
et pas seulement visuellement.

## 2. Authentification — écran unique dynamique

`login.html` est le **seul point d'entrée** de l'authentification. Connexion,
inscription multistep et mot de passe oublié partagent le même conteneur
(`.auth-stage`) piloté par une machine à états dans `assets/auth-flow.js` —
aucune des transitions n'entraîne de rechargement de page ni de navigation
vers une autre URL.

```
assets/
├── auth.css        → design system de base (tokens, boutons, champs)
├── auth-flow.css   → layout de l'écran unique (panneaux, skeleton, OTP,
│                      force du mot de passe, thème par rôle)
├── auth-flow.js    → machine à états + validation + appels API (mock ou réel)
├── auth-config.js  → apiBase, endpoints, politique d'invitation, réglages OTP
└── img/            → illustrations par contexte (connexion / inscription /
                       mot de passe oublié)
```

États gérés (voir `META` dans `auth-flow.js`) :
`login → reg-role → reg-personal → reg-password → reg-verify → reg-success`
et `login → forgot-email → forgot-verify → forgot-password → forgot-success`.
Chaque changement d'état met à jour : le panneau formulaire actif, l'illustration,
le titre/sous-titre, la barre de progression et la couleur d'accent (selon le
rôle choisi à l'inscription) — le panneau illustration et le panneau formulaire
échangent réellement leur position (translation CSS) entre Connexion et
Inscription, et restent groupés pour Mot de passe oublié.

- **Connexion** : e-mail + mot de passe. Le **rôle n'est plus choisi
  manuellement** dans le formulaire — il est déterminé par le compte
  (renvoyé par `/auth/login`). En mode démo, il est déduit de l'e-mail
  (contient "admin"/"notaire" → admin, "clerc" → clerc, sinon collaborateur).
- **Inscription** (4 étapes) : type de compte (avec **code d'invitation
  obligatoire**, y compris pour Notaire · Admin — jamais de confiance
  accordée à un rôle envoyé depuis le front) → informations personnelles →
  mot de passe (règles + indicateur de force en direct) → vérification par
  code OTP à 6 chiffres (saisie auto, collage, renvoi avec minuteur,
  compteur de tentatives) → écran de succès.
- **Mot de passe oublié** (3 étapes) : e-mail → code OTP → nouveau mot de
  passe → succès. Reste dans le même environnement visuel que la connexion
  (seule l'illustration change) — accessible via `login.html?go=forgot-email`.
- **OAuth Google / Apple** : boutons sur l'écran de connexion. Le frontend
  ne fait que déclencher le flux ; en production, l'échange de code et la
  décision "compte existant → connexion" / "nouveau compte → inscription"
  doivent être gérés côté serveur (jamais de token stocké côté client).
- **Session** : au succès (connexion, inscription, OAuth), `localStorage`
  reçoit `ged_session` : `{ token, role, name, email, ts }`, puis
  redirection vers `1-notaire-admin/`, `2-clerc-principal/` ou
  `3-collaborateur/` selon le rôle renvoyé par le serveur.
- **Garde de session** : chaque page des 3 espaces vérifie au chargement
  (`requireSession()` dans `app.js`) qu'une session valide existe et que son
  rôle correspond au dossier consulté ; sinon, redirection vers `../login.html`.
- **Déconnexion** : bouton "Se déconnecter" de `profil.html` (id `logoutLink`),
  vide la session et renvoie vers `login.html`.
- `inscription.html` est conservée en simple redirection vers
  `login.html?go=reg-role` pour les liens/favoris existants.

### Basculer sur un vrai backend

Dans `assets/auth-config.js` et dans chaque `assets/config.js` :

```js
useMock: false,
apiBase: "https://votre-api.example.com/api"
```

Quand `useMock` est à `false`, tous les appels passent par `fetch()` avec
`credentials:"include"`. Il suffit d'implémenter les routes ci-dessous — le
mapping complet (chemins, corps attendu, réponse) est déjà déclaré dans
`assets/auth-config.js`.

## 3. Contrat API attendu

### Authentification
| Méthode | Route | Corps | Réponse attendue |
|---|---|---|---|
| POST | `/auth/login` | `{ email, password }` | `{ token, role, name, email }` |
| POST | `/auth/register/check-invite` | `{ role, inviteCode }` | `{ valid, organizationName }` |
| POST | `/auth/register/start` | `{ role, inviteCode, firstName, lastName, email, phone, jobTitle }` | `{ ok:true }` |
| POST | `/auth/register/send-code` | `{ email }` | `{ expiresInSeconds }` |
| POST | `/auth/register/verify-code` | `{ email, code }` | `{ ok:true }` |
| POST | `/auth/register/complete` | `{ email, password }` | `{ token, role, name, email }` |
| POST | `/auth/forgot-password/send-code` | `{ email }` | `{ expiresInSeconds }` |
| POST | `/auth/forgot-password/verify-code` | `{ email, code }` | `{ resetToken }` |
| POST | `/auth/forgot-password/reset` | `{ resetToken, password }` | `{ ok:true }` |
| — | `/auth/oauth/google`, `/auth/oauth/apple` | — | flux OAuth/OIDC géré côté serveur |

**Sécurité attendue côté backend** (non implémentée ici, front-end uniquement) :
rôle **toujours** revalidé côté serveur à partir du code d'invitation (jamais
du `role` envoyé par le client) ; hashage des mots de passe (Argon2id/bcrypt) ;
expiration + invalidation des codes OTP à usage unique ; limitation du nombre
de tentatives et de demandes de code ; rate limiting sur `/auth/*` ; sessions
et cookies sécurisés ; protection CSRF ; journalisation des évènements
sensibles (création de compte admin, réinitialisation de mot de passe).

### Documents & recherche
| Méthode | Route |
|---|---|
| GET | `/documents?type=...` / `?niveau=...` |
| GET | `/documents/{ref}` · `/documents/{ref}/export` |
| POST | `/documents/{ref}/validate` |
| POST | `/documents/archive` · `/documents/favorite` |
| GET | `/search?q=...&type=...` |
| GET | `/documents/queue/{nom}` |

### Dossiers, utilisateurs, permissions
| Méthode | Route |
|---|---|
| POST | `/dossiers` |
| POST | `/users` |
| POST | `/permissions` · DELETE | `/permissions` |
| POST | `/access-requests` |

### Administration (Notaire uniquement)
| Méthode | Route |
|---|---|
| PUT | `/settings` |
| POST | `/backups/restore-test` |
| GET | `/audit/export?scope=cabinet\|me` |

### Divers
| Méthode | Route |
|---|---|
| PATCH | `/me` (mise à jour du profil) |
| PATCH | `/notifications/read` |
| POST | `/context/filiale` |

Toutes les réponses d'erreur doivent renvoyer un statut HTTP standard
(`401` session expirée, `403` droits insuffisants) : `app.js` les intercepte
déjà et affiche un toast adapté.

## 4. Responsive

Chaque feuille de style (`assets/styles.css` par rôle, `assets/auth.css` pour
l'authentification) est mobile-first avec paliers à `1180px`, `980px`,
`860px`, `480px` et `360px` : navigation en tiroir sous 860px, grilles qui se
replient en colonne unique, tableaux en défilement horizontal, formulaires
en une colonne, et taille de police forcée à 16px sur les champs en mobile
pour éviter le zoom automatique de Safari iOS.

## 5. Identité visuelle par interface

| Espace | Couleur dominante |
|---|---|
| Notaire · Admin | Ambre `#F7941E` |
| Clerc principal | Bleu `#3B6FDB` |
| Collaborateur | Vert `#1FA87A` |
| Connexion / Inscription | Indigo premium `#5B6CFF` (identité propre à l'accueil) |
