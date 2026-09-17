FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-fra \
    tesseract-ocr-eng \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt gunicorn

COPY backend /app/backend
COPY --chown=1000:1000 . /app/frontend
RUN mkdir -p /app/backend/media /app/backend/staticfiles /app/backend/backup_store \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

WORKDIR /app/backend
COPY backend/deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
USER appuser

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
