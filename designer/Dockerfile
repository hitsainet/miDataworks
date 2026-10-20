# hitsai/midataworks-designer (PADR ADR-010 amendment, 2026-10-07; ADR-024). Built in CI on the
# mirror only; never locally. A Celery worker consuming the `designer` queue, concurrency two so a
# preview never waits behind a long step. The Data-Juicer pattern exactly: its own pins
# (data-designer 0.9.4 needs huggingface-hub<2, which the backend's pins forbid), only its runner
# and the self-contained relay copied from backend/src, no database credentials. A model key
# reaches it sealed in Redis (DESIGNER_HANDOFF_KEY), never in a message.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data/dataworks
RUN useradd --create-home --uid 10001 dataworks
WORKDIR /app

COPY designer/requirements.txt designer/constraints.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/constraints.txt

# Only the runner and its relay: they import nothing else from src
# (backend/tests/unit/test_designer_runner_imports.py).
COPY backend/src/__init__.py /app/src/__init__.py
COPY backend/src/operators/__init__.py /app/src/operators/__init__.py
COPY backend/src/operators/data_designer/__init__.py backend/src/operators/data_designer/runner.py backend/src/operators/data_designer/relay.py /app/src/operators/data_designer/

USER dataworks
CMD ["celery", "-A", "src.operators.data_designer.runner", "worker", "-Q", "designer", "-n", "designer@%h", "--concurrency", "2"]
