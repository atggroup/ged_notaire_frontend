# PRA / PCA — GED Cabinet Notarial

Répond au point du cadrage : *« Ajouter PRA/PCA — le serveur physique du
cabinet tombe en panne, la GED doit prévoir comment récupérer : les
documents, les métadonnées, les comptes, les droits, l'audit, les clés de
chiffrement. »*

- **PCA (Plan de Continuité d'Activité)** : comment le cabinet continue à
  fonctionner *pendant* un incident.
- **PRA (Plan de Reprise d'Activité)** : comment le système est restauré
  *après* un sinistre, et en combien de temps.

Ce document définit le cadre ; les valeurs cibles (RTO/RPO) et les
prestataires doivent être validés par le cabinet avant mise en production.

## 1. Ce qui doit pouvoir être reconstitué

Un sinistre sur le serveur principal ne doit faire perdre aucun des
éléments suivants, tous nécessaires à un redémarrage complet et cohérent :

| Élément | Où il vit aujourd'hui | Mécanisme de récupération |
|---|---|---|
| Documents chiffrés (les binaires) | `backend/media/documents/` | Sauvegarde des fichiers + base (voir §3) |
| Métadonnées (dossiers, versions, statuts) | Base PostgreSQL | Sauvegarde/restauration de base |
| Comptes et droits (utilisateurs, permissions) | Base PostgreSQL | Idem — dans le même dump que les métadonnées |
| Journal d'audit append-only | Base PostgreSQL (`audit_auditlog`) | Idem, avec vérification d'intégrité après restauration |
| Clés de chiffrement | Gestionnaire de secrets externe, **pas** la base | Copie de secours séparée (voir §5) |

**Point d'attention critique** : les clés de chiffrement ne sont **pas**
stockées dans la base de données. Une restauration de base sans les clés
correspondantes rend tous les documents illisibles. Le plan de sauvegarde
des clés (§5) est donc aussi important que celui de la base.

## 2. Objectifs cibles (à valider avec le cabinet)

- **RPO (perte de données maximale tolérée)** : proposition initiale
  ≤ 24 h (une sauvegarde quotidienne complète + journal de transactions
  PostgreSQL en continu si l'hébergement le permet, pour réduire ce délai).
- **RTO (délai de reprise maximal toléré)** : proposition initiale
  ≤ 4 h ouvrées pour un redémarrage sur infrastructure de secours.

Ces chiffres sont des points de départ à ajuster selon le volume de
dossiers en cours et la criticité opérationnelle du cabinet.

## 3. Architecture de sauvegarde recommandée

Le cadrage souligne à raison qu'une simple « copie locale + copie cloud »
est insuffisante pour un cabinet notarial. Architecture cible :

1. **Copie primaire** : base + fichiers sur le serveur de production.
2. **Copie secondaire locale** : réplique quotidienne sur un support
   distinct (autre disque/serveur), pour un incident matériel simple.
3. **Copie tertiaire hors site** : réplication chiffrée vers un
   hébergement distinct (`BACKUP_CLOUD_ENABLED=true` une fois le
   prestataire réellement configuré), pour un sinistre touchant le local du
   cabinet (incendie, dégât des eaux, vol).
4. **Copie isolée/immuable** (recommandée, à mettre en place avec
   l'hébergeur) : au moins une copie non modifiable pendant une période
   donnée (write-once ou air-gapped), pour se prémunir d'un scénario de
   type rançongiciel où un attaquant chiffrerait ou supprimerait aussi les
   sauvegardes accessibles en écriture.

L'application expose déjà, pour la copie primaire, un contrôle d'intégrité
non destructif : `POST /api/backups/restore-test` déchiffre chaque document,
recalcule son empreinte SHA-256 et compare au hash stocké à l'upload, sans
jamais écrire dans les données de production. Le tableau de bord
Sauvegarde (`GET /api/backups/status`) affiche le résultat du dernier test
et l'historique des 20 derniers.

**Ce test de restauration vérifie l'intégrité des fichiers, pas la capacité
réelle à restaurer une base PostgreSQL sur une nouvelle machine** : un test
de restauration complet (base + fichiers + clés, sur une machine
indépendante) doit être organisé périodiquement (trimestriel recommandé) en
plus du test applicatif automatique.

## 4. Procédure de reprise (PRA) — trame

En cas de panne du serveur physique du cabinet :

1. **Confinement** : couper l'accès au serveur défaillant si le sinistre
   est en cours (incendie, intrusion) ; ne pas tenter d'écrire dessus.
2. **Provisionnement** d'une nouvelle instance (backend Django +
   PostgreSQL) selon `README_BACKEND.md`.
3. **Restauration de la base** depuis la dernière sauvegarde valide
   (copie secondaire ou tertiaire selon la nature du sinistre).
4. **Restauration des fichiers chiffrés** (`media/documents/`) depuis la
   même génération de sauvegarde que la base — **une base et des fichiers
   de générations différentes produiraient des incohérences de version**.
5. **Restauration des clés de chiffrement** depuis leur copie de secours
   séparée (§5) — sans elles, les étapes suivantes échouent.
6. **Vérification d'intégrité** : lancer `POST /api/backups/restore-test`
   puis `GET /api/audit/integrity` pour confirmer que les documents et le
   journal d'audit sont cohérents et non altérés.
7. **Reprise de service** : redémarrage de l'application, vérification
   qu'un notaire peut se connecter (MFA inclus) et ouvrir un document de
   test.
8. **Communication interne** : informer les collaborateurs du délai de
   coupure et de tout dossier potentiellement affecté (perte entre la
   dernière sauvegarde et l'incident, le cas échéant, mesurée par le RPO).
9. **Post-mortem** : consigner l'incident, sa cause, sa durée réelle, et
   mettre à jour ce document si la procédure a montré des lacunes.

## 5. Sauvegarde et récupération des clés de chiffrement

- Conserver au minimum **deux copies indépendantes** des clés actives
  (`DOCUMENT_ENCRYPTION_KEYS`), dans des supports/emplacements distincts de
  la base de données et de préférence distincts entre eux (ex. coffre-fort
  logiciel de l'hébergeur + coffre physique ou gestionnaire de secrets
  d'un second prestataire).
- Documenter précisément qui peut déclencher une récupération d'urgence
  (procédure à deux personnes recommandée) et comment.
- Après toute rotation de clé (voir `CONFORMITE_RGPD_ARTCI.md` §7),
  répliquer immédiatement la nouvelle clé vers ces copies de secours — une
  clé générée mais non sauvegardée est un point de défaillance unique.

## 6. Continuité pendant l'incident (PCA)

Pendant que la reprise est en cours, le cabinet doit pouvoir continuer un
minimum d'activité :

- Procédure dégradée papier pour les actes urgents, avec ressaisie dans la
  GED une fois le service rétabli (journalisée comme un import tardif).
- Liste à jour des contacts d'urgence (hébergeur, éditeur AIS/ATG,
  responsable sécurité du cabinet).
- Accès de secours à une liste des dossiers en cours (export périodique
  hors ligne, par exemple un export chiffré mensuel des dossiers actifs via
  `GET /api/dossiers/{reference}/export`, à conserver de façon sécurisée),
  pour permettre au moins une consultation minimale pendant l'indisponibilité.

## 7. Test et révision du plan

- Un test de restauration applicatif automatique existe déjà et doit être
  exécuté régulièrement (au minimum mensuel) : `POST
  /api/backups/restore-test`.
- Un exercice de reprise complet (base + fichiers + clés, sur une machine
  isolée) doit être planifié au moins une fois par an, avec mesure du RTO
  réel obtenu et mise à jour de ce document si l'objectif n'est pas atteint.
