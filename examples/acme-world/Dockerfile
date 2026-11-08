FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONUNBUFFERED=1 \
    ACME_WORLD_DB=/data/db.json \
    ACME_WORLD_FIXTURE=/fixtures/acme.json \
    ACME_NOW=2026-01-31 \
    ACME_REFUND_WINDOW_DAYS=30

WORKDIR /srv
COPY app/ /srv/app/
COPY evals/ /srv/evals/
COPY fixtures/ /fixtures/

EXPOSE 8000
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8000"]
