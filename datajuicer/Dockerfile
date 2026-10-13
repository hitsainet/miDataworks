# hitsai/midataworks-datajuicer (ADR-010, ADR-024). Built in CI on the mirror only; never locally.
# A Celery worker consuming the `datajuicer` and `datajuicer_preview` queues (feature 003), with
# concurrency two so a preview never waits behind a long step (ADR-006). It carries no backend dependencies and no
# database credentials: rows arrive and leave as Parquet paths on the shared data volume.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data/dataworks
RUN useradd --create-home --uid 10001 dataworks
WORKDIR /app

# T-10: the full resolved set is pinned (constraints.txt) and installed HERE, CPU torch included;
# nothing is installed while the worker runs.
COPY datajuicer/requirements.txt datajuicer/constraints.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/constraints.txt

# Only the runner package: it imports nothing else from src (tests/unit/test_datajuicer_runner_imports.py).
COPY backend/src/__init__.py /app/src/__init__.py
COPY backend/src/operators/__init__.py /app/src/operators/__init__.py
COPY backend/src/operators/datajuicer/ /app/src/operators/datajuicer/

# Image-time check (T-10): run every offered operator on the contract fixture with Data-Juicer's
# installer replaced by a refusal, and fail the build if anything would be installed. The check
# files are laid out as in the repository, then removed.
COPY datajuicer/dj_paths.py datajuicer/contract.py datajuicer/contract_cases.json /check/datajuicer/
COPY datajuicer/scripts/check_no_runtime_installs.py /check/datajuicer/scripts/
COPY backend/src/operators/datajuicer/catalogue.json /check/backend/src/operators/datajuicer/
COPY backend/tests/fixtures/operators/contract_fixture.parquet /check/backend/tests/fixtures/operators/
# `src` resolves to /app/src (a regular package on PYTHONPATH wins over the namespace directory
# the catalogue copy creates under /check/backend).
RUN DATA_DIR=/tmp/dj-check PYTHONPATH=/app python /check/datajuicer/scripts/check_no_runtime_installs.py \
    && rm -rf /check /tmp/dj-check

USER dataworks
CMD ["celery", "-A", "src.operators.datajuicer.runner", "worker", "-Q", "datajuicer,datajuicer_preview", "-n", "datajuicer@%h", "--concurrency", "2"]
