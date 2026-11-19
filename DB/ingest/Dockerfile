# Ingestion service. Build context is the repository root (it needs docs/contracts):
#   docker build -f DB/ingest/Dockerfile .
# infra/server/docker-compose.yml does this for you.

# --- build: install the pinned dependencies into a virtualenv ---------------------------------
FROM python:3.11.18-slim-trixie AS build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
COPY DB/ingest/requirements.txt /tmp/requirements.txt
RUN python -m venv /venv && /venv/bin/pip install -r /tmp/requirements.txt

# --- runtime: only the virtualenv, the code and the schemas; runs as a non-root user ---------
FROM python:3.11.18-slim-trixie

ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONTRACTS_DIR=/app/contracts \
    HEALTH_FILE=/tmp/ingest-heartbeat

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin ingest

COPY --from=build /venv /venv
WORKDIR /app
COPY docs/contracts/*.schema.json /app/contracts/
COPY DB/ingest/ingest /app/ingest

USER 10001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["python", "-m", "ingest.health"]

CMD ["python", "-m", "ingest"]
