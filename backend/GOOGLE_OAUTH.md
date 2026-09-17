# Connexion "Continuer avec Google" — guide sans jargon

Ce document explique, étape par étape, comment fonctionne et se règle la
connexion Google de l'application. Il est déjà configuré pour un usage sur
cet ordinateur (`http://127.0.0.1:8000`) ; vous n'avez rien à faire pour
l'instant. Gardez ce guide pour le jour où vous voudrez mettre
l'application en ligne sur un vrai nom de domaine (ex. `ged.mon-etude.ci`).

## Ce qui a déjà été fait pour vous

Google vous a remis un fichier "client_secret_....json" — c'est en quelque
sorte la "carte d'identité" que Google donne à l'application pour la
reconnaître. Les informations de ce fichier ont été copiées dans un
fichier de réglages interne à l'application, `backend/.env` :

- `GOOGLE_OAUTH_CLIENT_ID` — l'identifiant public de l'application.
- `GOOGLE_OAUTH_CLIENT_SECRET` — le mot de passe secret associé ; ne le
  partagez jamais, ne le mettez jamais sur un site, ne l'envoyez jamais par
  e-mail en clair.
- `GOOGLE_OAUTH_REDIRECT_URI` — l'adresse exacte vers laquelle Google
  renvoie la personne une fois connectée. Elle doit être identique, au
  caractère près, à celle déclarée dans Google Cloud Console (voir plus
  bas). Actuellement : `http://127.0.0.1:8000/auth/oauth/google/callback`.

## Le jour où vous changez d'adresse (mise en ligne réelle)

Si un jour l'application n'est plus seulement sur cet ordinateur mais sur
une vraie adresse internet (par exemple `https://ged.mon-etude.ci`), deux
choses doivent changer ensemble :

1. **Dans Google Cloud Console** (le site où le fichier "client_secret" a
   été obtenu au départ, https://console.cloud.google.com/apis/credentials,
   projet "ged-notaire") : ouvrez l'identifiant OAuth existant et ajoutez
   la nouvelle adresse dans "URI de redirection autorisés", par exemple :
   `https://ged.mon-etude.ci/auth/oauth/google/callback`
   (gardez l'ancienne en plus si vous testez encore en local).

2. **Dans le fichier `backend/.env`** de l'application : changez la ligne
   `GOOGLE_OAUTH_REDIRECT_URI` pour qu'elle corresponde exactement à la
   nouvelle adresse ajoutée à l'étape 1.

Si les deux ne correspondent pas exactement (même majuscules, même `/` à
la fin ou non), Google refusera la connexion — c'est volontaire, c'est une
protection contre l'usurpation.

## Pourquoi seulement Google (pas Apple, pas GitHub) ?

- **Apple** exige un abonnement développeur payant (99 $/an) rien que pour
  proposer "Se connecter avec Apple" — il a donc été retiré pour ne pas
  imposer de coût récurrent inutile à l'étude.
- **GitHub** est un outil réservé aux développeurs, que les clercs et
  collaborateurs de l'étude n'ont pas de raison de connaître ou de
  posséder — il n'a jamais été proposé.
- **Google**, en revanche, est gratuit et presque tout le monde en a déjà
  un (Gmail). C'est pour cela qu'il est la seule option de connexion
  sociale.

## Comment l'application choisit le rôle (clerc ou collaborateur) ?

- Jamais automatiquement pour une personne inconnue : si quelqu'un clique
  sur "Continuer avec Google" pour la toute première fois sans avoir
  d'abord choisi "Clerc principal" ou "Collaborateur", l'application lui
  demande de choisir avant de créer le compte.
- Le compte **Notaire · Administrateur** ne peut jamais être créé par ce
  chemin, même par erreur — c'est contrôlé par le programme lui-même, pas
  par une case à cocher qu'on pourrait oublier.
- Si l'adresse e-mail Google correspond à un compte qui existe déjà
  (classique, avec mot de passe, y compris un compte Notaire·Admin créé
  par `create_first_admin`), l'application connecte la personne à ce
  compte existant avec son rôle réel — elle ne crée jamais de second
  compte pour la même adresse e-mail.

## En cas de souci

- **"Connexion Google indisponible"** : le fichier `backend/.env` n'a
  probablement pas (ou plus) de `GOOGLE_OAUTH_CLIENT_ID` /
  `GOOGLE_OAUTH_CLIENT_SECRET` renseignés.
- **Google affiche une erreur "redirect_uri_mismatch"** : l'adresse dans
  `GOOGLE_OAUTH_REDIRECT_URI` (fichier `.env`) et celle déclarée dans
  Google Cloud Console ne sont pas identiques au caractère près.
- **Le lien Google a expiré** : la connexion Google doit se terminer dans
  les 10 minutes (choix du type de compte compris) ; c'est volontaire pour
  la sécurité — il suffit de recommencer.
