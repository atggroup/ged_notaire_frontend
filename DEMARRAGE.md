# Démarrer l'application GED Notaire — guide simple

Ce guide ne suppose aucune connaissance en informatique. Vous n'avez besoin
de rien d'autre que ce dossier et un ordinateur.

## Ce dont vous avez besoin une seule fois

1. Installez **Python** (version 3.12 ou plus récente) : allez sur
   https://www.python.org/downloads/ , téléchargez, puis lancez l'installeur.
   ⚠️ Sur l'écran d'installation, cochez la case **"Add Python to PATH"**
   avant de cliquer sur "Install Now".
2. C'est tout. Pas besoin de créer de compte, pas besoin de GitHub, pas
   besoin d'installer quoi que ce soit d'autre.

## Démarrer l'application

1. Décompressez (dézippez) ce dossier là où vous voulez sur votre ordinateur.
2. Ouvrez le dossier `backend` qui se trouve à l'intérieur.
3. Dans ce dossier `backend`, ouvrez un terminal :
   - **Windows** : dans la barre d'adresse de l'explorateur de fichiers,
     tapez `cmd` puis appuyez sur Entrée.
   - **Mac** : clic droit dans le dossier → "Nouveau terminal au dossier"
     (ou ouvrez l'app Terminal puis tapez `cd ` et glissez le dossier dedans).
4. Copiez-collez ces commandes une par une, en appuyant sur Entrée après
   chacune (la première installation prend quelques minutes) :

   ```
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver 127.0.0.1:8000
   ```

   💡 Ces trois commandes suffisent sur un ordinateur dédié à cette seule
   application. Si vous préférez isoler l'installation dans un
   "environnement virtuel" (recommandé si ce même ordinateur sert aussi à
   d'autres logiciels Python), voir l'encadré ci-dessous — mais ce n'est
   pas obligatoire.

   > **Environnement virtuel (optionnel)** — avant les trois commandes
   > ci-dessus, lancez :
   > ```
   > python -m venv .venv
   > .venv\Scripts\activate
   > ```
   > (sur Mac/Linux : `source .venv/bin/activate` à la place de la seconde
   > ligne). Si `python -m venv .venv` semble bloqué plusieurs minutes sur
   > Windows (antivirus qui scanne), n'appuyez pas sur Ctrl+C : patientez.
   > Si ça échoue quand même, supprimez le dossier `.venv` créé à moitié
   > (`rmdir /s /q .venv`) et repartez directement des trois commandes
   > sans environnement virtuel — ça fonctionne tout aussi bien ici.

5. Laissez cette fenêtre ouverte (c'est le "serveur" — le programme qui fait
   tourner l'application). Ouvrez votre navigateur internet (Chrome, Edge…)
   à l'adresse :

   ```
   http://127.0.0.1:8000/login.html
   ```

6. Les jours suivants, vous n'avez plus besoin de refaire toutes les étapes :
   ouvrez juste le terminal dans le dossier `backend` et relancez :
   - `python manage.py runserver 127.0.0.1:8000` directement si vous n'avez
     pas utilisé d'environnement virtuel ;
   - ou `.venv\Scripts\activate` puis `python manage.py runserver
     127.0.0.1:8000` si vous en avez créé un.

## Créer le tout premier compte (le Notaire · Administrateur)

Le tout premier compte de l'étude ne peut pas être créé depuis l'écran
d'inscription (qui ne propose que Clerc principal / Collaborateur, par
sécurité). Dans le terminal, toujours dans le dossier `backend` :

```
python manage.py create_first_admin
```

Un mot de passe temporaire s'affiche à l'écran — notez-le, il ne sera
montré qu'une seule fois. Connectez-vous ensuite avec cet e-mail et ce mot
de passe sur `http://127.0.0.1:8000/login.html`.

## La connexion "Continuer avec Google"

Elle est déjà configurée et prête à l'emploi (voir `GOOGLE_OAUTH.md` dans le
dossier `backend` si vous devez un jour la reconfigurer, par exemple pour
mettre l'application en ligne sur un vrai nom de domaine plutôt que sur cet
ordinateur).

- Un clerc ou un collaborateur qui clique sur **"Continuer avec Google"**
  pour la toute première fois choisit son type de compte à l'écran, puis
  son compte est créé automatiquement — sans mot de passe à retenir.
- S'il se reconnecte plus tard avec le même compte Google, il retombe
  directement sur son espace habituel, avec le rôle qui lui a déjà été
  attribué.
- Si son adresse e-mail a déjà un compte classique (avec mot de passe), la
  connexion Google se lie simplement à ce compte existant : jamais de
  doublon créé.
- Le compte **Notaire · Administrateur** n'est jamais créé via Google — il
  n'existe que via `create_first_admin` ou une création manuelle par un
  administrateur déjà connecté.

## Stockage des documents scannés

Chaque document scanné est chiffré puis enregistré sur le **serveur de
l'étude** (le poste ou le petit serveur qui fait tourner l'application),
avec une **copie de sauvegarde répliquée sur un espace cloud** distinct.
L'écran "Sauvegarde" (espace Notaire · Admin) montre l'état des deux copies
séparément. La mise en place effective de la réplication cloud (quel
prestataire, quelle fréquence) est une étape d'installation à faire une
fois avec votre prestataire informatique — ce dossier prépare
l'application pour ça, mais ne peut pas choisir un prestataire cloud à
votre place.
