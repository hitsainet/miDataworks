"""Two bounded lanes, ``interactive`` and ``batch`` (FTDD §7.2, B6 (a)).

Each lane is a thread pool of ``slots`` workers guarded by a semaphore of the same size. A unit of
work waits for a slot at most the wall-time backstop of its request; past it the unit answers
``rule_worker_unavailable`` without running, so a long build never starves an agent's dry run and a
burst of dry runs never stalls a build. Wasmtime releases the GIL while a guest runs (S0 2.6), so
threads in one process give parallelism (``RUNNER_PROCESSES = 1``).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


class LaneFull(Exception):
    """No slot became free within the backstop."""


class Lane:
    def __init__(self, name: str, slots: int) -> None:
        self.name = name
        self.slots = slots
        self._semaphore = threading.BoundedSemaphore(slots)
        self._pool = ThreadPoolExecutor(max_workers=slots, thread_name_prefix=f"lane-{name}")
        self._busy = 0
        self._lock = threading.Lock()

    @property
    def busy(self) -> int:
        with self._lock:
            return self._busy

    def submit(self, work: Callable[[], T], wait_s: float) -> Future[T]:
        """Run ``work`` on a free slot, waiting at most ``wait_s`` for one (else ``LaneFull``)."""
        if not self._semaphore.acquire(timeout=max(0.0, wait_s)):
            raise LaneFull(self.name)
        with self._lock:
            self._busy += 1

        def done(_: Future[T]) -> None:
            with self._lock:
                self._busy -= 1
            self._semaphore.release()

        future = self._pool.submit(work)
        future.add_done_callback(done)
        return future

    def snapshot(self) -> dict[str, int]:
        return {"slots": self.slots, "busy": self.busy}

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
