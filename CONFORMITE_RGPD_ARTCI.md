# Conformité « données personnelles » — GED Cabinet Notarial

Ce document répond au point du cadrage : *« La GED contiendra nécessairement
des données personnelles, potentiellement très sensibles selon les dossiers.
Il faudra donc documenter au minimum : finalités, catégories, accès,
conservation, sécurité, sous-traitants, hébergement, sauvegarde, incidents,
droits des personnes. »*

Il constitue un point de départ technique et organisationnel. **Il ne
remplace pas un avis juridique** : le notaire, responsable de traitement,
doit le faire valider et compléter par son conseil et, le cas échéant, le
déclarer/l'adapter auprès de l'**ARTCI** (Autorité de Régulation des
Télécommunications/TIC de Côte d'Ivoire), autorité de contrôle au titre de
la **loi ivoirienne n° 2013-450 du 19 juin 2013** relative à la protection
des données à caractère personnel.

## 1. Responsable de traitement

Le notaire titulaire de l'étude (ou le cabinet notarial constitué en
société) est responsable de traitement au sens de la loi n° 2013-450.
AIS/ATG Group intervient comme **sous-traitant technique** (éditeur et,
selon le contrat d'hébergement retenu, hébergeur) : un contrat de
sous-traitance (DPA — Data Processing Agreement) doit formaliser cette
relation, ses obligations de sécurité, et les conditions de sortie/
restitution des données en fin de contrat.

## 2. Finalités des traitements

| Finalité | Base légale usuelle | Exemple de donnée traitée |
|---|---|---|
| Instruction et rédaction des actes notariés | Obligation légale / mission notariale | Identité des parties, biens, montants |
| Gestion administrative des dossiers (GED) | Intérêt légitime du cabinet | Statut du dossier, pièces jointes |
| Sécurité du système d'information | Obligation légale (art. 17 et s., loi 2013-450) | Journal d'audit, adresses IP |
| Facturation et comptabilité de l'étude | Obligation légale | Coordonnées bancaires si applicable |
| Gestion des comptes utilisateurs internes | Intérêt légitime | Identité, rôle, connexions des collaborateurs |

Le système ne doit **jamais** être utilisé pour des finalités non déclarées
(prospection commerciale, profilage, cession à un tiers) sans base légale et
information des personnes concernées.

## 3. Catégories de données traitées

- **Identité des parties et clients** : nom, prénom, date/lieu de naissance,
  nationalité, adresse, pièce d'identité, situation familiale.
- **Données patrimoniales et financières liées à l'acte** : biens, valeurs,
  origine de fonds, éléments fiscaux — données sensibles au sens économique,
  à traiter avec le niveau de confidentialité le plus restrictif adapté
  (voir la matrice des 4 niveaux dans le cadrage fonctionnel).
- **Documents numérisés** : peuvent contenir, selon les dossiers, des
  données dites sensibles au sens large (santé dans un acte de tutelle,
  filiation, situation judiciaire dans une succession litigieuse, etc.).
  Ces catégories imposent systématiquement un niveau *Confidentiel* ou
  *Très confidentiel*, jamais *Standard*.
- **Données de connexion et d'usage** : adresse IP, horodatage, action,
  conservées dans le journal d'audit append-only (`audit.AuditLog`).
- **Données des collaborateurs** : identité, rôle, e-mail professionnel,
  historique de connexion — traitées au titre de la gestion du personnel et
  de la sécurité du système.

## 4. Minimisation et accès

Le principe RBAC + droits par dossier + exceptions documentaires (déjà
implémenté dans `permissions_app`) applique par construction le principe de
minimisation : un collaborateur ne voit que les dossiers auxquels il est
affecté ou pour lesquels une autorisation explicite existe. Le clerc ne peut
jamais lui-même élever le niveau de confidentialité d'un document
(`documents/views.py::upload_document`) — seul le notaire administrateur le
peut, ce qui matérialise le principe de *protection des données dès la
conception et par défaut* mis en avant par l'ARTCI.

## 5. Durées de conservation

Les durées de conservation légale des actes et pièces notariales relèvent de
la réglementation professionnelle notariale ivoirienne (délais de
conservation, obligations d'archivage centenaire pour certains actes, etc.)
et **doivent être fixées par le cabinet et son conseil**, pas par
l'application. Le système fournit les outils techniques permettant
d'appliquer *n'importe quelle* politique retenue :

- statuts de cycle de vie (`brouillon → … → validé → clos → archivé`) ;
- corbeille non destructive (`corbeille → demande de destruction →
  autorisation`), aucune purge binaire automatique ;
- gel de dossier (`legal_hold`) empêchant toute archivage/destruction tant
  qu'un contentieux, contrôle ou obligation de conservation spéciale est en
  cours.

**Action attendue du cabinet** : documenter, dossier par domaine
(§4 du référentiel), la durée de conservation active + la durée
d'archivage intermédiaire + le sort final (destruction autorisée /
versement à un service d'archives), puis en dériver une politique
opérationnelle (qui autorise une destruction, à quelle échéance).

## 6. Sécurité des données

Mesures déjà en place dans le backend :

- Chiffrement au repos AES-256-GCM de chaque fichier, avec **trousseau de
  clés versionné** permettant une rotation sans jamais rendre un document
  existant illisible (`documents/crypto.py`) ;
- Authentification renforcée (MFA e-mail) pour le notaire/admin et pour tout
  compte ayant un accès confidentiel ;
- Verrouillage de compte après échecs répétés, révocation immédiate de
  session, interdiction de sur-provisionnement via Google (identité
  seulement, jamais d'autorisation) ;
- Journal d'audit append-only à chaîne de hachage vérifiable
  (`audit.AuditIntegrityView`) ;
- Empreinte SHA-256 par version de document, vérifiée à chaque test de
  restauration (`settings_app.RestoreTestView`).

Mesures à formaliser par le cabinet (hors périmètre applicatif) :

- politique de mots de passe et de rotation des clés de chiffrement
  (calendrier, responsable, procédure — voir §7) ;
- chiffrement en transit : HTTPS obligatoire en production
  (`ged_backend.settings.prod`) ;
- gestion des habilitations physiques aux postes et aux salles d'archives.

## 7. Gestion des clés de chiffrement

Qui détient les clés ? C'est une décision du cabinet, pas de l'éditeur.
Recommandations opérationnelles :

- Stocker `DOCUMENT_ENCRYPTION_KEY(S)` dans un gestionnaire de secrets
  externe (coffre-fort logiciel type Vault, ou service géré de
  l'hébergeur), jamais dans un fichier versionné ni partagé par e-mail.
- Séparer la personne qui génère/détient les clés de celle qui administre
  l'application (séparation des tâches).
- Planifier une rotation périodique : générer une nouvelle entrée dans
  `DOCUMENT_ENCRYPTION_KEYS`, basculer `DOCUMENT_ENCRYPTION_ACTIVE_KEY_ID`,
  puis **ne jamais retirer une ancienne clé tant qu'un document actif
  l'utilise encore** (visible via `GET /api/dashboard/summary` →
  `chiffrement.knownKeyIds` pour le notaire).
- Conserver une copie de secours des clés hors ligne, accessible selon une
  procédure d'urgence à deux personnes minimum (« double contrôle »).
- Documenter la procédure de récupération d'urgence en cas de perte d'accès
  au gestionnaire de secrets principal — voir `PRA_PCA.md`.

## 8. Sous-traitants et hébergement

À compléter par le cabinet avant mise en production :

| Sous-traitant | Rôle | Localisation des données | Contrat |
|---|---|---|---|
| AIS / ATG Group | Éditeur, développement, maintenance | — | À formaliser |
| Hébergeur (à choisir) | Hébergement serveur/base/fichiers | À préciser | DPA à signer |
| Fournisseur de messagerie sortante (SMTP) | Envoi des OTP et notifications | À préciser | DPA à signer |
| Prestataire de sauvegarde externe (si `BACKUP_CLOUD_ENABLED`) | Réplication hors site | À préciser | DPA à signer |

## 9. Incidents de sécurité

En cas de violation de données (perte, accès non autorisé, compromission) :

1. Confiner l'incident (révocation de sessions, rotation immédiate de la
   clé de chiffrement concernée, désactivation des comptes impliqués).
2. Constituer un dossier factuel à partir du journal d'audit append-only
   (export `GET /api/audit/export?scope=cabinet`).
3. Évaluer l'obligation de notification à l'ARTCI et, le cas échéant, aux
   personnes concernées, dans les délais prévus par la réglementation
   applicable — décision à prendre avec le conseil du cabinet.
4. Documenter l'incident et les mesures correctives dans un registre dédié.

## 10. Droits des personnes concernées

Lorsqu'applicables (accès, rectification, opposition dans les limites de la
mission notariale et des obligations légales de conservation), les demandes
doivent être adressées au notaire responsable de traitement. Le système
permet techniquement de retrouver l'ensemble des documents liés à une
personne (recherche par partie, export de dossier avec bordereau) pour
instruire ces demandes, mais la décision d'y donner suite relève du notaire
et de son conseil, notamment en raison des obligations légales de
conservation qui peuvent primer sur une demande d'effacement.

## 11. Registre des traitements

Ce document doit être complété par un registre des traitements formel
(modèle ARTCI ou modèle interne du cabinet), tenu à jour, récapitulant pour
chaque traitement : finalité, catégories de données, catégories de
personnes, destinataires, durée de conservation, mesures de sécurité. Ce
registre est distinct de la présente note et reste à la charge du cabinet.
