# PRA / PCA — GED notariale

## Objectif
Restaurer la GED sans modifier la production avant validation humaine.

## Sauvegarde (automatique)
L'exécutant `sauvegarde` (`python manage.py run_worker --groupe sauvegarde`) crée toutes les 6 h (`BACKUP_INTERVAL_SECONDS`) une copie contenant :
- les documents chiffrés (tels que stockés, chiffrés AES-256-GCM) ;
- `database.json.enc` : instantané chiffré de la base (comptes, droits, métadonnées, audit) ;
- `manifest.json` : index de vérification **sans métadonnées parlantes** (références opaques, empreintes) ;
- `manifest.details.enc` : métadonnées détaillées (noms de fichiers, dossiers, statuts), chiffrées.

Si le cloud S3 est configuré, le même jeu est répliqué hors site.
Rotation automatique : toutes les sauvegardes des dernières 24 h, puis la plus récente de chaque jour (7), semaine (4) et mois (12). La plus récente et la dernière vérifiée ne sont jamais supprimées ; rien n'est supprimé hors du dépôt de sauvegardes.

En cas d'échec : nouvelle tentative à 30 min (2 fois), puis **alerte critique** aux notaires. Le contrôle horaire `fraicheur_sauvegarde` alerte si aucune sauvegarde réussie n'a moins de 2 × l'intervalle.

Ponctuel : `python manage.py backup_ged` (code de sortie non nul en cas d'échec — utilisable par le Planificateur de tâches Windows pour une copie vers un disque externe).

## Test périodique (automatique)
Chaque dimanche à 04:00, `exercice_pra` vérifie la dernière sauvegarde : empreintes de la base, des métadonnées et de chaque binaire, déchiffrement avec la clé de l'étude. Le résultat est enregistré (historique de l'écran Sauvegarde), journalisé et notifié. Ponctuel : `python manage.py restore_drill`.

Ce test ne remplace pas un exercice de reprise complet sur une machine isolée (au moins annuel, avec mesure du RTO réel).

## Ordre de reprise
1. Restaurer la base de données.
2. Restaurer les clés depuis le secret-store ou le paquet de récupération.
3. Restaurer les documents chiffrés.
4. Vérifier les droits/RBAC et les affectations.
5. Vérifier la chaîne d'audit (`/api/audit/integrity`, ou attendre le contrôle quotidien de 03:30).
6. Redémarrer les exécutants et vérifier l'écran **Sauvegarde · Supervision** (tous les travaux au vert).
7. Réaliser un contrôle fonctionnel avant remise en service.

## Clés
Les clés de chiffrement ne sont pas intégrées aux sauvegardes ordinaires. Utiliser `key-management/recovery-export` pour produire un paquet chiffré par phrase secrète et conserver celui-ci séparément des sauvegardes de données. Une clé encore référencée par un document ne doit jamais être retirée.

## RPO/RTO
Les valeurs RPO/RTO sont configurables dans l'application. Les valeurs par défaut sont 60 minutes / 240 minutes et doivent être validées par le cabinet. **Attention** : avec une sauvegarde toutes les 6 h, le RPO réel est de 6 h ; réduire `BACKUP_INTERVAL_SECONDS` si l'objectif de 60 minutes est retenu.
