"""Background jobs: what a job type does, and the worker that runs them.

A request that would take long (reading a big PDF, embedding thousands of chunks) can ask for
`"background": true`: the API stores a job in MongoDB, answers `202` at once, and a worker does the work.
The client follows it with `GET /api/v1/jobs/{id}`. The worker is a loop that can run inside the API
process (the default) or on its own (`python worker.py`), several of them side by side if needed.
"""
import asyncio
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from helpers.config import Settings
from helpers.exceptions import DomainError
from models.JobModel import JobModel
from services.documents import Outcome, index_project_chunks, process_project_files

logger = logging.getLogger("uvicorn.error")

PROCESS_DOCUMENTS = "process_documents"
INDEX_DOCUMENTS = "index_documents"

GENERIC_ERROR = "The job failed. Try again, and quote the job id if it keeps failing."


@dataclass
class JobContext:
    """The clients a job needs. `Services` has the same fields, so either can be passed to a handler."""
    mongo_db: Any
    generation_client: Any
    embedding_client: Any
    vectordb_client: Any
    template_parser: Any


class JobFailed(Exception):
    """Raised by a handler for a failure that retrying will not fix. `message` is shown to the user."""

    def __init__(self, message: str, result: Optional[dict] = None):
        super().__init__(message)
        self.message = message
        self.result = result


Handler = Callable[[Any, dict, Callable[[int, int], Awaitable[None]]], Awaitable[dict]]


def _outcome_result(outcome: Outcome) -> dict:
    """A finished job's result; an Outcome that is an error ends the job as failed (retrying won't change it)."""
    if not outcome.ok:
        raise JobFailed(outcome.body.get("signal", GENERIC_ERROR), outcome.body)
    return outcome.body


async def handle_process_documents(context, job: dict, progress) -> dict:
    payload = job["payload"]
    return _outcome_result(await process_project_files(
        context.mongo_db, job["project_id"], payload.get("file_id"), payload["chunk_size"],
        payload["overlap_size"], payload["do_reset"], progress=progress))


async def handle_index_documents(context, job: dict, progress) -> dict:
    return _outcome_result(await index_project_chunks(
        context, job["project_id"], job["payload"]["do_reset"], progress=progress))


HANDLERS: dict[str, Handler] = {
    PROCESS_DOCUMENTS: handle_process_documents,
    INDEX_DOCUMENTS: handle_index_documents,
}


class JobWorker:
    """Claims jobs from the queue and runs them, up to `JOB_CONCURRENCY` at a time."""

    STOP_GRACE_SECONDS = 30

    def __init__(self, context, db, settings: Settings, handlers: Optional[dict] = None,
                 worker_id: Optional[str] = None):
        self.context = context
        self.settings = settings
        self.handlers = handlers or HANDLERS
        self.jobs = JobModel(db)
        self.worker_id = worker_id or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self._stopping = asyncio.Event()
        self._running: set[asyncio.Task] = set()

    # ── lifecycle ────────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Work until `stop()`. A database error is logged and retried, never fatal."""
        logger.info(f"Job worker {self.worker_id} started (concurrency {self.settings.JOB_CONCURRENCY})")
        while not self._stopping.is_set():
            claimed = False
            try:
                while len(self._running) < self.settings.JOB_CONCURRENCY and not self._stopping.is_set():
                    job = await self.jobs.claim_next(
                        self.worker_id, self.settings.JOB_LEASE_SECONDS, list(self.handlers),
                        self.settings.JOB_RETENTION_DAYS)
                    if job is None:
                        break
                    claimed = True
                    task = asyncio.create_task(self._run_job(job))
                    self._running.add(task)
                    task.add_done_callback(self._running.discard)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Job worker could not reach the queue", exc_info=True)
            if not claimed:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self.settings.JOB_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass
        logger.info(f"Job worker {self.worker_id} stopping")

    async def stop(self) -> None:
        """Stop taking jobs and let the running ones finish (up to a grace period; the rest are retried later)."""
        self._stopping.set()
        if self._running:
            _, pending = await asyncio.wait(set(self._running), timeout=self.STOP_GRACE_SECONDS)
            for task in pending:
                task.cancel()  # its lease runs out and another worker picks the job up
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    # ── one job ──────────────────────────────────────────────────────────────
    async def _run_job(self, job: dict) -> None:
        job_id, settings = job["_id"], self.settings
        handler = self.handlers[job["type"]]

        async def progress(done: int, total: int) -> None:
            await self.jobs.set_progress(job_id, self.worker_id, done, total)

        work = asyncio.create_task(handler(self.context, job, progress))
        beat = asyncio.create_task(self._keep_lease(job_id, work))
        try:
            result = await work
            await self.jobs.complete(job_id, self.worker_id, result, settings.JOB_RETENTION_DAYS)
            logger.info(f"Job {job_id} ({job['type']}) succeeded")
        except asyncio.CancelledError:
            work.cancel()
            raise
        except JobFailed as exc:
            await self.jobs.fail(job_id, self.worker_id, exc.message, retry=False,
                                 retention_days=settings.JOB_RETENTION_DAYS, result=exc.result)
            logger.warning(f"Job {job_id} ({job['type']}) failed: {exc.message}")
        except DomainError as exc:
            status = await self.jobs.fail(job_id, self.worker_id, exc.public_message, retry=True,
                                          retention_days=settings.JOB_RETENTION_DAYS)
            logger.warning(f"Job {job_id} ({job['type']}) hit {exc.code}; now {status}")
        except Exception:
            status = await self.jobs.fail(job_id, self.worker_id, GENERIC_ERROR, retry=True,
                                          retention_days=settings.JOB_RETENTION_DAYS)
            logger.error(f"Job {job_id} ({job['type']}) crashed; now {status}", exc_info=True)
        finally:
            beat.cancel()

    async def _keep_lease(self, job_id, work: asyncio.Task) -> None:
        """Renew the lease while the job runs. If it was lost (we were too slow), stop the work: another worker owns it."""
        interval = max(self.settings.JOB_LEASE_SECONDS / 3, 1)
        while not work.done():
            await asyncio.sleep(interval)
            try:
                if not await self.jobs.heartbeat(job_id, self.worker_id, self.settings.JOB_LEASE_SECONDS):
                    logger.warning(f"Job {job_id} was taken over by another worker; abandoning it here")
                    work.cancel()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(f"Could not renew the lease of job {job_id}", exc_info=True)


# ── submitting ───────────────────────────────────────────────────────────────
def project_name_of(scoped_project_id: str, user_id: str) -> str:
    """The name the user gave a project, from its storage id (see helpers/project_scope.py)."""
    prefix = f"u{user_id}p"
    return scoped_project_id[len(prefix):] if scoped_project_id.startswith(prefix) else scoped_project_id


async def submit_job(db, settings: Settings, job_type: str, user_id: str, project_id: str, payload: dict):
    """Queue a job for a project. Returns (public job, created); `created` is False when one is already waiting
    or running for that project, in which case that job is returned instead of a duplicate."""
    job, created = await JobModel(db).enqueue(
        job_type, user_id, project_id, project_name_of(project_id, user_id), payload, settings.JOB_MAX_ATTEMPTS)
    return JobModel.public(job), created
