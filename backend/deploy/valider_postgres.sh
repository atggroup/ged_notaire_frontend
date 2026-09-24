#!/bin/sh
# Valide l'application sur un vrai PostgreSQL jetable, puis nettoie tout.
# À lancer depuis le dossier backend/ :  sh deploy/valider_postgres.sh
# Code de sortie 0 = validation réussie.
set -u
cd "$(dirname "$0")/.."
docker compose -f docker-compose.test.yml run --rm --build tests
resultat=$?
docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1
if [ "$resultat" -eq 0 ]; then
  echo "VALIDATION POSTGRESQL RÉUSSIE — migrations, contrôles et tests passent sur PostgreSQL 16."
else
  echo "VALIDATION POSTGRESQL ÉCHOUÉE (code $resultat) — NE PAS DÉPLOYER, transmettre la sortie ci-dessus."
fi
exit "$resultat"
