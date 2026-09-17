#!/bin/sh
set -eu

python manage.py migrate --noinput
python manage.py check --deploy
python manage.py collectstatic --noinput

if [ "${CREATE_INITIAL_ADMIN:-false}" = "true" ]; then
  python manage.py create_first_admin || true
fi

exec gunicorn ged_backend.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${WEB_CONCURRENCY:-3}" \
  --threads "${GUNICORN_THREADS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-180}" \
  --access-logfile - \
  --error-logfile -
