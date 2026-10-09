#!/bin/sh
# Entry point for hitsai/midataworks-backend (ADR-024; Foundation task 15.1).
#
# One image, several processes, chosen by SERVICE_TYPE:
#   api     alembic upgrade head, then uvicorn on :8000. Only the API migrates, so two
#           containers never race on the schema.
#   worker  a Celery worker on CELERY_QUEUES (required; no default, because a worker with the
#           wrong queue list consumes nothing and looks healthy). ADR-006's queue groups are
#           assigned in k8s/base/backend.yaml.
#   beat    Celery Beat (janitor, queued-job dispatch).
#   mcp     the MCP server (feature 010, ADR-016): python -m src.mcp_server on :8765. Needs
#           MCP_AUTH_TOKEN and DATAWORKS_API_URL, and nothing else: no database, no Redis.
# Arguments, if given, are run instead (for one-off commands in the image).
#
# Refuses rather than guesses: an unknown SERVICE_TYPE or a worker without queues exits non-zero
# with the reason, so the pod crash-loops visibly instead of idling.
# backend/tests/unit/test_backend_image.py runs this script against stub executables.
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

CELERY_APP="src.core.celery_app"

case "${SERVICE_TYPE:-}" in
    api)
        echo "[entrypoint] alembic upgrade head"
        alembic upgrade head
        echo "[entrypoint] starting the API on :${API_PORT:-8000}"
        exec uvicorn src.main:app --host 0.0.0.0 --port "${API_PORT:-8000}" \
            --proxy-headers --forwarded-allow-ips '*'
        ;;
    worker)
        if [ -z "${CELERY_QUEUES:-}" ]; then
            echo "[entrypoint] REFUSING: SERVICE_TYPE=worker needs CELERY_QUEUES" >&2
            exit 64
        fi
        echo "[entrypoint] worker ${CELERY_WORKER_NAME:-worker} on queues ${CELERY_QUEUES}"
        exec celery -A "$CELERY_APP" worker \
            -Q "$CELERY_QUEUES" \
            -n "${CELERY_WORKER_NAME:-worker}@%h" \
            --pool "${CELERY_POOL:-prefork}" \
            --concurrency "${CELERY_CONCURRENCY:-1}" \
            --loglevel "${LOG_LEVEL:-INFO}"
        ;;
    beat)
        echo "[entrypoint] celery beat"
        exec celery -A "$CELERY_APP" beat \
            --schedule /tmp/celerybeat-schedule \
            --loglevel "${LOG_LEVEL:-INFO}"
        ;;
    mcp)
        echo "[entrypoint] starting the MCP server (backend ${DATAWORKS_API_URL:-unset})"
        exec python -m src.mcp_server
        ;;
    *)
        echo "[entrypoint] REFUSING: unknown SERVICE_TYPE '${SERVICE_TYPE:-}' (api, worker, beat, mcp)" >&2
        exit 64
        ;;
esac
