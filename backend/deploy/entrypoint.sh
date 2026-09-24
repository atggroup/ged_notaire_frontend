#!/bin/sh
set -eu

# Préparation du schéma et des fichiers statiques.
#
# Elle n'a de sens que pour le service applicatif : lancer `migrate` depuis
# trois conteneurs qui démarrent en même temps, c'est trois transactions
# concurrentes sur la même table de migrations. Les services annexes
# (sauvegarde, rappels) passent RUN_MIGRATIONS=false et attendent simplement
# que la base soit prête.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  python manage.py migrate --noinput
  # Table du cache partagé (compteurs de limitation de débit) ; idempotent.
  python manage.py createcachetable
  python manage.py check --deploy
  python manage.py collectstatic --noinput

  if [ "${CREATE_INITIAL_ADMIN:-false}" = "true" ]; then
    python manage.py create_first_admin || true
  fi
fi

# Si docker-compose fournit une commande (sidecar de sauvegarde, envoi des
# rappels), c'est ELLE qu'il faut exécuter.
#
# Sans ce passage de relais, le script ignorait ses arguments et lançait
# gunicorn quoi qu'il arrive : le conteneur « backup » faisait donc tourner un
# second serveur web au lieu de la boucle de sauvegarde. Autrement dit, la
# sauvegarde automatique de l'étude n'a jamais eu lieu, alors que l'écran de
# supervision laissait croire le contraire.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

exec gunicorn ged_backend.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-3}" \
  --threads "${GUNICORN_THREADS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile -
