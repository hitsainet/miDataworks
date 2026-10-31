# hitsai/midataworks-runner (feature 018; ADR-024). Built in CI on the mirror only; never locally.
# The isolated runner: a tiny HTTP server that runs operator code rules inside a WebAssembly guest
# on Wasmtime, with fuel, a memory cap and an epoch deadline, through its own WASI table (no
# preopened directory, empty environment, fixed clock). It carries NO database credentials, no
# Redis URL, no data volume and no secret (Property 1), and its NetworkPolicy denies egress
# (Property 2). It copies only the runner package, the contract package and the packaged contract
# file from backend/src — runner/tests/test_runner_imports.py pins that those import nothing else.
#
# The guest bundle is fetched at build time by its URL and SHA-256 READ FROM THE PACKAGED CONTRACT
# (no literal digest here; build_bundle.py refuses a mismatch), then precompiled to snapshot.cwasm
# for this image's Wasmtime and CPU. The .wasm/.cwasm are never committed (no_wasm_tracked test).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NVIDIA_VISIBLE_DEVICES=void
RUN useradd --create-home --uid 10001 runner
WORKDIR /app

COPY runner/requirements.txt runner/constraints.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt -c /app/constraints.txt

# Only the contract package, the runner package and the packaged contract (they import nothing else
# from src: runner/tests/test_runner_imports.py; nothing else in src imports the runner:
# backend/tests/unit/test_backend_never_imports_runner.py). Copied as root: the runner user can read
# them, never change them.
COPY backend/src/__init__.py /app/src/__init__.py
COPY backend/src/core/__init__.py backend/src/core/canonical_json.py /app/src/core/
COPY backend/src/runner_contract/ /app/src/runner_contract/
COPY backend/src/runner/ /app/src/runner/
COPY backend/src/schemas/__init__.py /app/src/schemas/__init__.py
COPY backend/src/schemas/data/midataworks-runner-v1.json /app/src/schemas/data/
COPY runner/scripts/build_bundle.py /app/runner/scripts/

# Fetch the published guest snapshot by digest (from the packaged contract) and precompile it.
# The bundle stays ROOT-OWNED and read-only (018 Review R1 L5): the precompiled snapshot.cwasm is
# native code loaded without a second digest check, so the process that runs guests must never be
# able to rewrite it. The pod's read-only root filesystem is the second layer, not the only one.
ENV RUNNER_BUNDLE_DIR=/app/bundle
RUN python /app/runner/scripts/build_bundle.py --out "$RUNNER_BUNDLE_DIR" \
    && chmod -R a-w,a+rX "$RUNNER_BUNDLE_DIR"

USER runner
EXPOSE 8080
CMD ["python", "-m", "src.runner.server"]
