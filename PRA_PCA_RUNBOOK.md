# PRA / PCA — GED notariale

## Objectif
Restaurer la GED sans modifier la production avant validation humaine.

## Sauvegarde
`python manage.py backup_ged` crée une copie locale contenant les documents chiffrés, `database.json` (comptes, droits, métadonnées et audit) et `manifest.json`. Si le cloud S3 est configuré, le même jeu est répliqué hors site.

## Test périodique
`python manage.py restore_drill` vérifie l'intégrité de la base et de chaque ciphertext présent dans la sauvegarde. Planifier cette commande avec le Planificateur de tâches Windows ou cron.

## Ordre de reprise
1. Restaurer la base de données.
2. Restaurer les clés depuis le secret-store ou le paquet de récupération.
3. Restaurer les documents chiffrés.
4. Vérifier les droits/RBAC et les affectations.
5. Vérifier la chaîne d'audit.
6. Réaliser un contrôle fonctionnel avant remise en service.

## Clés
Les clés de chiffrement ne sont pas intégrées aux sauvegardes ordinaires. Utiliser `key-management/recovery-export` pour produire un paquet chiffré par phrase secrète et conserver celui-ci séparément des sauvegardes de données. Une clé encore référencée par un document ne doit jamais être retirée.

## RPO/RTO
Les valeurs RPO/RTO sont configurables dans l'application. Les valeurs par défaut sont 60 minutes / 240 minutes et doivent être validées par le cabinet.
