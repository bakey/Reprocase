FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    SUPPORT_WORLD_DB=/data/db.json \
    SUPPORT_WORLD_FIXTURE=/fixtures/refund.json

WORKDIR /srv
COPY app/ /srv/app/
COPY fixtures/ /fixtures/

EXPOSE 8000
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8000"]
