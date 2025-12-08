from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Any, Dict, Optional

from ..metrics import metrics

logger = logging.getLogger(__name__)


@dataclass
class Job:
    id: str
    name: str
    fn: Callable[[], Any]
    enqueued_at: float
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    status: str = "queued"  # queued, running, done, failed
    error: Optional[str] = None


class InMemoryJobQueue:
    """Lightweight in-process job queue for background tasks."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[Job]" = queue.Queue()
        self._jobs: Dict[str, Job] = {}
        self._worker: Optional[threading.Thread] = None

    def enqueue(self, name: str, fn: Callable[[], Any]) -> Job:
        job = Job(id=str(uuid.uuid4()), name=name, fn=fn, enqueued_at=time.time())
        self._jobs[job.id] = job
        self._queue.put(job)
        metrics.job_queue_enqueued += 1
        if not self._worker or not self._worker.is_alive():
            self._start_worker()
        return job

    def _start_worker(self) -> None:
        def _worker_loop() -> None:
            while True:
                job = self._queue.get()
                job.started_at = time.time()
                job.status = "running"
                try:
                    job.fn()
                    job.status = "done"
                    metrics.job_queue_completed += 1
                except Exception as exc:  # pragma: no cover - defensive
                    job.status = "failed"
                    job.error = str(exc)
                    metrics.background_job_errors += 1
                    metrics.job_queue_failed += 1
                    logger.exception(
                        "job_failed", extra={"job_id": job.id, "name": job.name}
                    )
                finally:
                    job.finished_at = time.time()
                    self._queue.task_done()

        self._worker = threading.Thread(
            target=_worker_loop, name="job-queue-worker", daemon=True
        )
        self._worker.start()

    def stats(self) -> Dict[str, Any]:
        return {
            "queued": sum(1 for j in self._jobs.values() if j.status == "queued"),
            "running": sum(1 for j in self._jobs.values() if j.status == "running"),
            "done": sum(1 for j in self._jobs.values() if j.status == "done"),
            "failed": sum(1 for j in self._jobs.values() if j.status == "failed"),
        }

    def recent(self, limit: int = 20) -> list[Dict[str, Any]]:
        jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.enqueued_at, reverse=True)
        out = []
        for j in jobs[:limit]:
            out.append(
                {
                    "id": j.id,
                    "name": j.name,
                    "status": j.status,
                    "enqueued_at": j.enqueued_at,
                    "started_at": j.started_at,
                    "finished_at": j.finished_at,
                    "error": j.error,
                }
            )
        return out


job_queue = InMemoryJobQueue()
