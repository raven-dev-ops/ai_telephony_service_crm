from __future__ import annotations

import logging
import threading
import time
from queue import Queue, Empty
from typing import Callable, Any

from ..metrics import metrics

logger = logging.getLogger(__name__)


class JobQueue:
    """Simple in-process job queue with a background worker.

    This is intentionally lightweight and can be swapped out for Celery/RQ
    later by replacing this module and keeping the enqueue interface.
    """

    def __init__(self, poll_interval: float = 0.1) -> None:
        self._queue: Queue[tuple[Callable, tuple, dict]] = Queue()
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="job-worker"
        )
        self._thread.start()
        logger.info("job_queue_started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        logger.info("job_queue_stopped")

    def enqueue(self, fn: Callable | str, *args: Any, **kwargs: Any) -> None:
        """Enqueue a callable for background execution.

        Accepts either (callable, *args) or a legacy pattern where the first
        argument is a string job name followed by the callable.
        """
        target = fn
        remaining_args = args
        if isinstance(fn, str) and args and callable(args[0]):
            target = args[0]
            remaining_args = args[1:]
        if not callable(target):
            logger.warning("job_queue_enqueue_invalid_target", extra={"fn": repr(fn)})
            return
        self._queue.put((target, remaining_args, kwargs))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                job, args, kwargs = self._queue.get(timeout=self._poll_interval)
            except Empty:
                continue
            try:
                job(*args, **kwargs)
            except Exception:
                metrics.background_job_errors += 1
                logger.exception(
                    "background_job_failed",
                    extra={"job": getattr(job, "__name__", "unknown")},
                )
            finally:
                self._queue.task_done()
            time.sleep(self._poll_interval)


job_queue = JobQueue()
