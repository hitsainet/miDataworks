---
id: getting-started
title: Getting started
---

miDataworks has a Python backend (an API, Celery workers and Beat), a React user interface, and
PostgreSQL and Redis for state. It needs no GPU.

## What you need

- Docker, for PostgreSQL 15 and Redis 7 only;
- Python 3.11;
- Node.js 22 (LTS).

## Run it locally

From a clone of the repository:

```bash
# 1. Configuration. Fill in the two secrets, and point DATA_DIR at a directory you can write.
cp .env.example .env
#    SETTINGS_ENCRYPTION_KEY=$(openssl rand -hex 32)
#    INTERNAL_API_SECRET=$(openssl rand -hex 32)
#    DATA_DIR=$HOME/midataworks-data

# 2. PostgreSQL 15 on 127.0.0.1:55433 and Redis 7 on 127.0.0.1:56380.
docker compose up -d

# 3. Backend environment.
cd backend
python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
set -a; . ../.env; set +a
.venv/bin/alembic upgrade head
```

Then, each in its own terminal (run `set -a; . ../.env; set +a` in each, from `backend/`):

```bash
.venv/bin/uvicorn src.main:app --port 8000                                # the API
.venv/bin/celery -A src.core.celery_app worker \
    -Q default,curation,labeling,publish,preview -n dev@%h                # the workers
.venv/bin/celery -A src.core.celery_app worker \
    -Q imports -c 1 -n imports@%h                                          # imports, one at a time
.venv/bin/celery -A src.core.celery_app beat --schedule ./celerybeat-schedule   # Beat
```

And the user interface:

```bash
cd frontend
npm ci
npm run dev        # http://127.0.0.1:3100 — proxies /api to the API on :8000
```

`curl -s http://127.0.0.1:8000/api/health` reports each dependency. In the application, open the
operations drawer in the top bar and run the self-test job to see a job queue, report progress and
cancel.

## Jobs and the operations drawer

Imports, builds, label runs and publishes run as background jobs. **Show operations** in the top bar
lists running jobs with their progress, and failed jobs with the recorded reason. Failures from the
last day are shown first; older ones are folded away and can be dismissed together. Dismissing hides
a failure; its record is kept.

## Keep `SETTINGS_ENCRYPTION_KEY` safe

Every secret miDataworks stores (the Hugging Face token, endpoint keys) is encrypted with
`SETTINGS_ENCRYPTION_KEY`. Losing the key loses them; copy it somewhere safe once.

## Connecting the rest of the suite (optional)

Leave these unset and miDataworks runs standalone:

| Variable | What it enables |
|---|---|
| `MILLM_BASE_URL` | Probe-verdict and feature-tag label runs, steered pairs, and miLLM in the health report. |
| `MILLM_UI_URL` | Links from the Label step to miLLM's Models page when a probe needs a model loaded. |
| `MISTUDIO_BASE_URL` | Sending detector sets to miStudio, refreshing their results, and reward marks. |

Labelers, judges, generators and embeddings are configured in **Settings**, one card per role: any
OpenAI-compatible server, or a TEI server for classification and embeddings. See
[Label runs](guide/labeling.md#endpoint-roles).

## Next

Walk through [your first dataset](first-dataset.md).
