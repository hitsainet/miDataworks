# miDataworks

The data plane of the mi suite. miDataworks imports Hugging Face datasets, curates and labels them
(with a classifier or a judge, usually served by miLLM), checks labelers against human labels,
builds detector sets for miStudio and training sets for miForge, and publishes versions to the
Hugging Face Hub. miLLM serves models; miStudio trains detectors and interprets models; miForge
changes weights. miDataworks makes the data.

**Status:** Foundation (application shell, job infrastructure, CI and deployment). The feature
screens are registered and show which feature builds them.

| Part | Technology |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2 + Alembic on PostgreSQL 15, Celery 5 on Redis 7, Parquet + DuckDB for rows |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Zustand |
| Data-Juicer | Its own image and queue, with no database credentials |
| Deployment | Images built in CI, deployed by ArgoCD to Kubernetes; no GPU |

## Run it locally

You need Docker (for PostgreSQL and Redis only), Python 3.11 and Node.js 20. Images are never
built locally; CI builds them.

```bash
# 1. Configuration. Fill in the two secrets, and point DATA_DIR at a directory you can write.
cp .env.example .env
#    SETTINGS_ENCRYPTION_KEY=$(openssl rand -hex 32)
#    INTERNAL_API_SECRET=$(openssl rand -hex 32)
#    DATA_DIR=$HOME/midataworks-data

# 2. PostgreSQL 15 on 127.0.0.1:55433 and Redis 7 on 127.0.0.1:56380.
docker compose up -d

# 3. Backend environment (any Python 3.11; `uv venv --python 3.11 .venv` also works).
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
.venv/bin/celery -A src.core.celery_app beat --schedule /tmp/midataworks-beat   # Beat
```

And the UI:

```bash
cd frontend
npm ci
npm run dev        # http://127.0.0.1:3100 — proxies /api to the API on :8000
```

`curl -s http://127.0.0.1:8000/api/health` reports each dependency. From the UI, open the
operations drawer in the top bar and run the self-test job to see a job queue, report progress
and cancel.

## Tests

```bash
cd backend && .venv/bin/pytest tests -n 4 --dist loadfile      # the whole tree, as CI runs it
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
cd frontend && npm run e2e:install && npm run e2e               # Playwright, both colour modes
```

The backend tests use the database `midataworks_test` (each parallel worker makes its own copy)
and refuse to run against any database whose name lacks "test".

## Repository layout

| Path | Holds |
|---|---|
| `backend/` | API, workers, Beat and (feature 010) the MCP server; one image |
| `frontend/` | The UI |
| `datajuicer/` | The Data-Juicer worker image |
| `k8s/` | Kustomize base and the ArgoCD application |
| `docs/schemas/` | Shared contracts owned here (`midataworks.dataset-version/v1` arrives with feature 008) |
| `.github/workflows/` | CI, the public mirror sync, image builds, the deploy-branch sync |

## The prototype

Before this application existed, the same work was done by hand for one dataset: a humor-labelling
project using `autotrust/JEV-9B` served by miLLM. Its plan and work log (`PLAN-humor-labeling.md`),
scripts (`scripts/`) and run records (`records/`) are kept in the private source repository as the
reference for the labeling and detector features. They are not part of the application and are not
published in the public mirror.

## Licence

Apache-2.0.
