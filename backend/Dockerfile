# hitsai/midataworks-backend (ADR-024; Foundation task 15.1). Built in CI on the mirror only,
# never locally. ONE image serves the API, every Celery worker container, Beat and (feature 010)
# the MCP server; `SERVICE_TYPE` picks the process (docker-entrypoint.sh).
#
# No GPU and no CUDA base (BRD-03 §3): miDataworks runs no model of its own.
#
# NON-ROOT, unlike miStudio's backend, which starts as root and drops privileges in its
# entrypoint. Here the process is uid 10001 from the first instruction that runs it. The data
# volume is a hostPath that the kubelet creates root-owned, so the Kubernetes manifest runs a
# small root init container that hands the volume ROOT to uid 10001 before this image starts
# (k8s/base/backend.yaml). The Data-Juicer image uses the same uid, so both write one volume.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data/dataworks \
    HF_HOME=/data/dataworks/cache/hf \
    SERVICE_TYPE=api

RUN useradd --create-home --uid 10001 --user-group dataworks
WORKDIR /app

# Dependencies first, for layer caching. Exact pins (ADR-002), and constraints.txt pins the whole
# resolved set, so the image, CI and the dev venv install the same transitive versions;
# psycopg2-binary and asyncpg ship wheels, so no compiler or libpq headers are needed.
COPY requirements.txt constraints.txt /app/
RUN pip install --upgrade "pip>=25.2" && pip install -r /app/requirements.txt -c /app/constraints.txt
# Third-party operator packages, reviewed by commit (feature 003, T-10); empty to start.
COPY requirements-operators.txt /app/requirements-operators.txt
RUN pip install -r /app/requirements-operators.txt -c /app/constraints.txt

COPY --chown=dataworks:dataworks alembic.ini /app/alembic.ini
COPY --chown=dataworks:dataworks alembic/ /app/alembic/
COPY --chown=dataworks:dataworks src/ /app/src/
COPY --chown=dataworks:dataworks --chmod=0755 docker-entrypoint.sh /app/docker-entrypoint.sh

# The commit (or release tag) this image was built from. Settings.app_build reads APP_BUILD, and
# every publish manifest records it as producer.build. docker-images.yml passes it as a build
# argument; a build without one says "dev". Declared last so a new value rebuilds no layer above.
# No Kubernetes manifest may set APP_BUILD: it would override the image's value
# (tests/unit/test_backend_image.py).
ARG APP_BUILD=dev
ENV APP_BUILD=${APP_BUILD}

USER 10001:10001

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)" \
        || [ "$SERVICE_TYPE" != "api" ]

ENTRYPOINT ["/app/docker-entrypoint.sh"]
