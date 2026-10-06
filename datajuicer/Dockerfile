# hitsai/midataworks-datajuicer (ADR-010, ADR-024). Built in CI on the mirror only; never locally.
# A Celery worker consuming only the `datajuicer` queue. It carries no backend dependencies and no
# database credentials: rows arrive and leave as Parquet paths on the shared data volume.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data/dataworks
RUN useradd --create-home --uid 10001 dataworks
WORKDIR /app

COPY datajuicer/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Only the runner package: it imports nothing else from src (tests/unit/test_datajuicer_runner_imports.py).
COPY backend/src/__init__.py /app/src/__init__.py
COPY backend/src/operators/__init__.py /app/src/operators/__init__.py
COPY backend/src/operators/datajuicer/ /app/src/operators/datajuicer/

USER dataworks
CMD ["celery", "-A", "src.operators.datajuicer.runner", "worker", "-Q", "datajuicer", "-n", "datajuicer@%h", "--concurrency", "2"]
