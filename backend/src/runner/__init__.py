"""The isolated runner (feature 018): runs operator code rules inside a WebAssembly guest.

A separate process and image (``hitsai/midataworks-runner``). It imports only the standard library,
``wasmtime``, ``starlette``, ``uvicorn``, ``anyio`` and ``src.runner_contract.{constants, prelude,
reasons, hashing}`` (+ ``src.core.canonical_json``); ``runner/tests/test_runner_imports.py`` walks
the syntax trees and fails on anything else. Nothing else in ``src`` imports this package
(planned guard ``tests/unit/test_backend_never_imports_runner.py``, FTASKS 6.10 in S2; not
written yet). Run it with ``python -m src.runner.server``.
"""
