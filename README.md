# miDataworks

The data plane of the mi suite. miDataworks imports Hugging Face datasets, curates and labels them
(with a classifier or a judge, usually served by miLLM), checks labelers against human labels,
builds detector sets for miStudio and training sets for miForge, and publishes versions to the
Hugging Face Hub. miLLM serves models; miStudio trains detectors and interprets models; miForge
makes weights — creating them from scratch, changing them in place, or adding adapters that
compose with them. miDataworks makes the data.

**Manual:** <https://hitsainet.github.io/miDataworks/>

**Status:** version 0.1.0, a pre-release in implementation and acceptance. All fifteen planned
features are built and merged to `main`:

| # | Feature |
|---|---|
| 001 | Sources and import (Hugging Face datasets and file uploads) |
| 002 | Versions, recipes and provenance |
| 003 | Operator framework and engines (Data Designer, Data-Juicer) |
| 004 | Curation and the shortcut audit |
| 005 | Labeling and endpoints (classifiers and judges, usually served by miLLM) |
| 006 | Calibration and human review |
| 007 | Synthetic generation and steered pairs |
| 008 | Publishing, export and the handoff contract |
| 009 | Detector sets and the miStudio loop |
| 010 | The MCP (Model Context Protocol) server |
| 011 | miStudio provenance routes |
| 012 | Runtime environment settings |
| 013 | Help pages |
| 014 | Endpoint keyring and settings PIN |
| 015 | Behaviour dataset support |

Some acceptance checks are still open. Most need a live session: a GPU, a sibling application
(miStudio or miLLM) or a person doing the check by hand. miDataworks works without miStudio and
miLLM; the features that use them detect them at run time and say clearly when they are absent.

| Part | Technology |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 + Alembic on PostgreSQL 15, Celery 5 on Redis 7, Parquet + DuckDB for rows |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Zustand |
| Data Designer | Its own image and queue |
| Data-Juicer | Its own image and queue, with no database credentials |
| Deployment | Images built in CI, deployed by ArgoCD to Kubernetes; no GPU |

## Run it locally

You need Docker (for PostgreSQL and Redis only), Python 3.13 and Node.js 22 (LTS). Images are never
built locally; CI builds them.

```bash
# 1. Configuration. Fill in the two secrets, and point DATA_DIR at a directory you can write.
cp .env.example .env
#    SETTINGS_ENCRYPTION_KEY=$(openssl rand -hex 32)
#    INTERNAL_API_SECRET=$(openssl rand -hex 32)
#    DATA_DIR=$HOME/midataworks-data

# 2. PostgreSQL 15 on 127.0.0.1:55433 and Redis 7 on 127.0.0.1:56380.
docker compose up -d

# 3. Backend environment (any Python 3.13; `uv venv --python 3.13 .venv` also works).
cd backend
python3.13 -m venv .venv
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
cd manual && npm ci && npm run build                            # the manual; fails on a broken link or page
```

The backend tests use the database `midataworks_test` (each parallel worker makes its own copy)
and refuse to run against any database whose name lacks "test".

## Repository layout

| Path | Holds |
|---|---|
| `backend/` | API, workers, Beat and the MCP server; one image |
| `frontend/` | The UI |
| `designer/` | The Data Designer worker image |
| `datajuicer/` | The Data-Juicer worker image |
| `k8s/` | Kustomize base and the ArgoCD application |
| `docs/schemas/` | Shared contracts owned here (`midataworks.dataset-version/v1`, `midataworks.rubric/v1`) and shared test cases |
| `docs/*.md` | The user pages the manual is built from (`manual/publish-allowlist.json` names them) |
| `manual/` | The manual site (Docusaurus), published to <https://hitsainet.github.io/miDataworks/> |
| `.github/workflows/` | CI, the public mirror sync, image builds, the deploy-branch sync |

## The prototype

Before this application existed, the same work was done by hand for one dataset: a humor-labelling
project using `autotrust/JEV-9B` served by miLLM. Its plan and work log (`PLAN-humor-labeling.md`),
scripts (`scripts/`) and run records (`records/`) are kept in the private source repository as the
reference for the labeling and detector features. They are not part of the application and are not
published in the public mirror.

## Licence

Apache-2.0.
