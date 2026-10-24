---
id: getting-started
title: Getting started
---

miDataworks has a Python backend (an API, Celery workers and Beat), a React user interface, and
PostgreSQL and Redis for state. It needs no GPU.

## What you need

- Docker, for PostgreSQL 15 and Redis 7 only;
- Python 3.13;
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
OpenAI-compatible server, or a TEI (Text Embeddings Inference) server for classification and
embeddings. See [Label runs](guide/labeling.md#endpoint-roles).

The three addresses above can also be changed while miDataworks runs, under **Settings**,
**Environment** (see below).

## Settings

The **Settings** screen holds your name (recorded as who did each action), the Hugging Face token, one
card per endpoint role, the agent access rules, the shortcut warning level, and **Environment**.

**Environment** lists every setting the deployment's environment gives miDataworks, in four groups:

| Group | What you can do |
|---|---|
| Changeable while miDataworks runs | Change the value without restarting anything. Each row says when the change takes effect (for example *next request*, *next task*, *next janitor pass*), shows the environment's value beside the value in force, and who changed it last. **Use the environment value** removes the change. |
| Needs a restart | Read-only. Change it in the deployment and restart the backend, which stops every running job. |
| Approval rules | Read-only. When an agent's request waits for you is deployment configuration. |
| From the environment | Read-only. Secrets are shown only as set or not set, and passwords are removed from addresses. |

The environment stays the source of truth: a change made here is stored as an override, and removing
it returns the setting to the environment's value. Every change is kept in a history with who made
it, when, and the reason if one was given; the screen lists the last 50. Values are checked before
they are saved: a value outside a setting's range is refused with the reason. An AI agent can read
these settings; a change it asks for waits for your approval, and a few settings (the miLLM and
miStudio addresses and the steering switch) only you can change.

Changing a setting here never restarts a job. A running label or generation run picks up a change at
its next chunk.

### Agent requests and the Approvals screen

An AI agent connected through the Model Context Protocol (MCP) can ask miDataworks to do something
that needs your say first: pushing to the Hugging Face Hub, labeling many rows, changing a setting.
The request waits on the **Approvals** screen in the menu, where the number beside **Approvals** says
how many are waiting; a banner at the top of every other screen says so too, with a link to the
screen.

Each request names the agent, what it would do and when it expires. **Approve** runs it and
**Reject** refuses it; a request nobody decides expires, and the agent has to ask again. Below the
waiting requests, **Earlier agent requests** lists the ones already decided or expired; **Archive**
hides a rejected or expired one, and nothing is deleted.

### The Settings PIN

A personal identification number (PIN) of 4 to 12 digits can lock every change on the Settings
screen: the endpoints and their API keys, Environment, your name, the Hugging Face token and the
shortcut warning level, and approving an agent's request to change a setting (on the **Approvals**
screen). Set it under
**Settings PIN**; changing or removing it needs the current PIN. While a PIN is set, a change asks for
it once, and the browser tab then stays unlocked for 30 minutes (**Lock now** ends that early). The
PIN is checked by the server, not only by the page, and it is stored only as a salted hash.

The PIN is not a login: miDataworks has no user accounts. A change an AI agent asks for still waits
for your approval, and once you approve it, it runs without the PIN.

**Forgot the PIN?** Set `SETTINGS_PIN_BYPASS=true` in the backend's environment and restart it.
Settings is then open, and a new PIN can be set (or the PIN removed) without the current one. Remove
the variable and restart again afterwards. The bypass can only be switched on in the deployment, never
from the app.

### API keys stay with their server

Each endpoint role keeps one API key per server. Pointing a role at another server keeps the old key
for the old server and never sends it to the new one; pointing it back uses the old key again. See
[Label runs](guide/labeling.md#endpoint-roles).

## Next

Walk through [your first dataset](first-dataset.md).
