#!/bin/sh
# Entry point for hitsai/midataworks-backend (ADR-024; Foundation task 15.1).
#
# One image, several processes, chosen by SERVICE_TYPE:
#   api     `python -m src.core.migrate upgrade`, then uvicorn on :8000. Only the API migrates,
#           so two containers never race on the schema.
#   worker  a Celery worker on CELERY_QUEUES (required; no default, because a worker with the
#           wrong queue list consumes nothing and looks healthy). ADR-006's queue groups are
#           assigned in k8s/base/backend.yaml.
#   beat    Celery Beat (janitor, queued-job dispatch).
#   worker and beat first run `python -m src.core.migrate wait-for-head`, and do not start until
#   the schema is at this code's alembic head (WAIT_FOR_SCHEMA=false opts out).
#   mcp     the MCP server (feature 010, ADR-016): python -m src.mcp_server on :8765. Needs
#           MCP_AUTH_TOKEN and DATAWORKS_API_URL, and nothing else: no database, no Redis.
# Arguments, if given, are run instead (for one-off commands in the image).
#
# Refuses rather than guesses: an unknown SERVICE_TYPE or a worker without queues exits non-zero
# with the reason, so the pod crash-loops visibly instead of idling.
#
# MIGRATIONS ARE BOUNDED (miStudio's incident of 2026-10-21, the same exposure here). A bare
# `alembic upgrade head` waited forever on a lock held by a hung COPY, the startup probe SIGKILLed
# the container twice, each kill left its ALTER queued in Postgres, and the worker/beat containers
# (same pod, started at the same moment) queried a column the migration had not added yet.
#   - `python -m src.core.migrate upgrade` runs the migration under lock_timeout,
#     statement_timeout and a deadline, names whoever blocks it, retries a lock timeout with
#     backoff, and exits non-zero inside the startup-probe window (src/core/migration_safety.py;
#     k8s/base/backend.yaml).
#   - `python -m src.core.migrate wait-for-head` holds a worker/beat container until
#     alembic_version is this code's head (bounded by MIGRATION_WAIT_TIMEOUT, default 600 s).
# backend/tests/unit/test_backend_image.py runs this script against stub executables.
set -eu

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

CELERY_APP="src.core.celery_app"

# Workers and beat must not start against a schema older than their code.
wait_for_schema() {
    if [ "${WAIT_FOR_SCHEMA:-true}" = "false" ]; then
        return 0
    fi
    echo "[entrypoint] waiting for the database schema to reach this code's alembic head"
    if ! python -m src.core.migrate wait-for-head; then
        echo "[entrypoint] the schema never reached this code's head; not starting ${SERVICE_TYPE}" >&2
        exit 1
    fi
}

case "${SERVICE_TYPE:-}" in
    api)
        echo "[entrypoint] migrating (python -m src.core.migrate upgrade)"
        if ! python -m src.core.migrate upgrade; then
            echo "[entrypoint] migration FAILED (the reason, and any blocking session, is logged above); not starting the API" >&2
            exit 1
        fi
        echo "[entrypoint] starting the API on :${API_PORT:-8000}"
        exec uvicorn src.main:app --host 0.0.0.0 --port "${API_PORT:-8000}" \
            --proxy-headers --forwarded-allow-ips '*'
        ;;
    worker)
        if [ -z "${CELERY_QUEUES:-}" ]; then
            echo "[entrypoint] REFUSING: SERVICE_TYPE=worker needs CELERY_QUEUES" >&2
            exit 64
        fi
        wait_for_schema
        echo "[entrypoint] worker ${CELERY_WORKER_NAME:-worker} on queues ${CELERY_QUEUES}"
        exec celery -A "$CELERY_APP" worker \
            -Q "$CELERY_QUEUES" \
            -n "${CELERY_WORKER_NAME:-worker}@%h" \
            --pool "${CELERY_POOL:-prefork}" \
            --concurrency "${CELERY_CONCURRENCY:-1}" \
            --loglevel "${LOG_LEVEL:-INFO}"
        ;;
    beat)
        wait_for_schema
        # The schedule file records when each periodic task last ran. On the data
        # volume it survives a restart; in /tmp it did not, so beat counted every
        # interval from its own start and the daily prune-agent-requests never ran
        # on a pod redeployed more than once a day.
        beat_dir="${DATA_DIR:-/data/dataworks}/celerybeat"
        mkdir -p "$beat_dir"
        echo "[entrypoint] celery beat (schedule in $beat_dir)"
        exec celery -A "$CELERY_APP" beat \
            --schedule "$beat_dir/celerybeat-schedule" \
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
