"""The background job queue (plan 3.10): the MongoDB-backed queue, the worker, and the `background: true`
routes. Runs against a throwaway MongoDB database; skipped when none is reachable."""
import asyncio
import io
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from controllers.BaseController import BaseController
from helpers.auth_utils import get_current_user
from helpers.error_handlers import install_error_handlers
from helpers.exceptions import ExternalServiceError
from models import JobModel as job_module
from models.JobModel import FAILED, QUEUED, RUNNING, SUCCEEDED, JobModel
from models.indexes import ensure_all_indexes
from routes import data as data_routes, jobs as jobs_routes, nlp as nlp_routes
from services.jobs import GENERIC_ERROR, HANDLERS, PROCESS_DOCUMENTS, JobContext, JobFailed, JobWorker

MONGO_URL = "mongodb://localhost:27017/"


def _mongo_available() -> bool:
    import pymongo

    try:
        pymongo.MongoClient(MONGO_URL, serverSelectionTimeoutMS=1000).server_info()
        return True
    except Exception:
        return False


needs_mongo = pytest.mark.skipif(not _mongo_available(), reason="MongoDB is not reachable")

SETTINGS = SimpleNamespace(JOB_CONCURRENCY=2, JOB_POLL_SECONDS=0.05, JOB_LEASE_SECONDS=30,
                           JOB_RETENTION_DAYS=7, JOB_MAX_ATTEMPTS=3)


def run_with_db(coro_fn):
    """Run `coro_fn(db)` on a throwaway database with the queue's indexes, then drop it."""
    async def runner():
        conn = AsyncIOMotorClient(MONGO_URL)
        name = f"test_jobs_{uuid.uuid4().hex[:8]}"
        db = conn[name]
        try:
            await ensure_all_indexes(db)
            return await coro_fn(db)
        finally:
            await conn.drop_database(name)
            conn.close()

    return asyncio.run(runner())


async def enqueue(jobs, project="p1", user="u1", job_type="t", max_attempts=3, payload=None):
    job, created = await jobs.enqueue(job_type, user, project, "name", payload or {}, max_attempts)
    return job, created


# ── the queue ────────────────────────────────────────────────────────────────
@needs_mongo
def test_only_one_job_of_a_kind_is_active_per_project():
    async def scenario(db):
        jobs = JobModel(db)
        first, created_first = await enqueue(jobs)
        again, created_again = await enqueue(jobs)
        other_project, created_other = await enqueue(jobs, project="p2")
        other_type, created_type = await enqueue(jobs, job_type="u")
        assert (created_first, created_again, created_other, created_type) == (True, False, True, True)
        assert again["_id"] == first["_id"]

        claimed = await jobs.claim_next("w1", 30)
        await jobs.complete(claimed["_id"], "w1", {}, 7)
        later, created_later = await enqueue(jobs)  # the finished one no longer blocks a new request
        assert created_later and later["_id"] != first["_id"]

    run_with_db(scenario)


@needs_mongo
def test_claims_are_oldest_first_and_each_job_goes_to_exactly_one_worker():
    async def scenario(db):
        jobs = JobModel(db)
        ids = [(await enqueue(jobs, project=f"p{i}"))[0]["_id"] for i in range(3)]

        claims = await asyncio.gather(*[jobs.claim_next(f"w{i}", 30) for i in range(10)])
        got = [c["_id"] for c in claims if c is not None]
        assert sorted(got) == sorted(ids)            # all three, none twice, though ten workers raced
        assert all(c["status"] == RUNNING and c["attempts"] == 1 for c in claims if c)

        assert await jobs.claim_next("w1", 30) is None  # nothing left

    run_with_db(scenario)


@needs_mongo
def test_a_job_whose_worker_went_silent_is_taken_over_and_the_old_worker_cannot_finish_it():
    async def scenario(db):
        jobs = JobModel(db)
        job, _ = await enqueue(jobs)
        await jobs.claim_next("slow", 30)
        await db["jobs"].update_one({"_id": job["_id"]}, {"$set": {"lease_expires_at": datetime.utcnow() - timedelta(seconds=1)}})

        taken = await jobs.claim_next("fast", 30)
        assert taken["_id"] == job["_id"] and taken["worker_id"] == "fast" and taken["attempts"] == 2

        assert await jobs.complete(job["_id"], "slow", {"late": True}, 7) is False   # not its job any more
        assert await jobs.heartbeat(job["_id"], "slow", 30) is False
        assert await jobs.fail(job["_id"], "slow", "x", retry=True, retention_days=7) == "lost"
        assert await jobs.complete(job["_id"], "fast", {"ok": True}, 7) is True

    run_with_db(scenario)


@needs_mongo
def test_a_job_that_keeps_killing_its_worker_is_failed_not_retried_for_ever():
    async def scenario(db):
        jobs = JobModel(db)
        job, _ = await enqueue(jobs, max_attempts=2)
        for attempt in (1, 2):
            claimed = await jobs.claim_next("w", 30)
            assert claimed["attempts"] == attempt
            await db["jobs"].update_one({"_id": job["_id"]}, {"$set": {"lease_expires_at": datetime.utcnow() - timedelta(seconds=1)}})

        assert await jobs.claim_next("w", 30) is None
        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert stored["status"] == FAILED and "interrupted" in stored["error"] and "active_key" not in stored
        assert (await enqueue(jobs))[1] is True      # and it no longer blocks a new job

    run_with_db(scenario)


@needs_mongo
def test_a_failed_job_is_retried_after_a_delay_until_its_attempts_run_out(monkeypatch):
    async def scenario(db):
        jobs = JobModel(db)
        job, _ = await enqueue(jobs, max_attempts=2)

        await jobs.claim_next("w", 30)
        assert await jobs.fail(job["_id"], "w", "boom", retry=True, retention_days=7) == QUEUED
        assert await jobs.claim_next("w", 30) is None        # waits for its backoff

        await db["jobs"].update_one({"_id": job["_id"]}, {"$set": {"run_after": datetime.utcnow()}})
        again = await jobs.claim_next("w", 30)
        assert again["attempts"] == 2
        assert await jobs.fail(job["_id"], "w", "boom", retry=True, retention_days=7) == FAILED

        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert stored["error"] == "boom" and stored["expires_at"] is not None and "active_key" not in stored

    run_with_db(scenario)


@needs_mongo
def test_users_only_see_their_own_jobs_and_the_public_view_hides_internals():
    async def scenario(db):
        jobs = JobModel(db)
        mine, _ = await enqueue(jobs, user="alice", payload={"secret_path": "/x"})
        await enqueue(jobs, user="bob", project="p2")

        assert (await jobs.get_for_user(str(mine["_id"]), "alice"))["_id"] == mine["_id"]
        assert await jobs.get_for_user(str(mine["_id"]), "bob") is None
        assert await jobs.get_for_user("not-an-id", "alice") is None
        assert [j["user_id"] for j in await jobs.list_for_user("alice")] == ["alice"]

        public = JobModel.public(mine)
        assert set(public) == {"job_id", "type", "project", "status", "progress", "result", "error", "attempts",
                               "created_at", "finished_at"}
        assert "secret_path" not in str(public) and "alice" not in str(public)

    run_with_db(scenario)


# ── the worker ───────────────────────────────────────────────────────────────
async def wait_until(predicate, timeout=10.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if await predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not reached in time")


async def status_of(db, job_id):
    return (await db["jobs"].find_one({"_id": job_id}))["status"]


def start_worker(db, handlers, settings=SETTINGS, worker_id="w1"):
    worker = JobWorker(None, db, settings, handlers=handlers, worker_id=worker_id)
    return worker, asyncio.create_task(worker.run())


@needs_mongo
def test_the_worker_runs_a_job_and_records_progress_and_result():
    async def scenario(db):
        async def handler(context, job, progress):
            await progress(1, 2)
            await progress(2, 2)
            return {"done": job["payload"]["x"]}

        jobs = JobModel(db)
        job, _ = await enqueue(jobs, payload={"x": 7})
        worker, task = start_worker(db, {"t": handler})
        await wait_until(lambda: _is(db, job["_id"], SUCCEEDED))
        await worker.stop()
        await task

        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert stored["result"] == {"done": 7} and stored["progress"] == {"done": 2, "total": 2}
        assert stored["finished_at"] and stored["expires_at"] and "active_key" not in stored

    run_with_db(scenario)


async def _is(db, job_id, status):
    return await status_of(db, job_id) == status


@needs_mongo
def test_an_unexpected_crash_is_retried_and_never_leaks_its_message(monkeypatch):
    monkeypatch.setattr(job_module, "MAX_BACKOFF_SECONDS", 0)

    async def scenario(db):
        calls = []

        async def handler(context, job, progress):
            calls.append(job["attempts"])
            raise RuntimeError("could not open /srv/secret/path.pdf")

        job, _ = await enqueue(JobModel(db), max_attempts=3)
        worker, task = start_worker(db, {"t": handler})
        await wait_until(lambda: _is(db, job["_id"], FAILED))
        await worker.stop()
        await task

        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert calls == [1, 2, 3]
        assert stored["error"] == GENERIC_ERROR and "secret" not in str(JobModel.public(stored))

    run_with_db(scenario)


@needs_mongo
def test_a_domain_error_is_retried_with_its_public_message(monkeypatch):
    monkeypatch.setattr(job_module, "MAX_BACKOFF_SECONDS", 0)

    async def scenario(db):
        calls = []

        async def handler(context, job, progress):
            calls.append(1)
            if len(calls) < 2:
                raise ExternalServiceError("internal detail", service="embeddings")
            return {"ok": True}

        job, _ = await enqueue(JobModel(db))
        worker, task = start_worker(db, {"t": handler})
        await wait_until(lambda: _is(db, job["_id"], SUCCEEDED))
        await worker.stop()
        await task
        assert len(calls) == 2

    run_with_db(scenario)


@needs_mongo
def test_a_failure_that_retrying_cannot_fix_is_final_and_keeps_its_result():
    async def scenario(db):
        calls = []

        async def handler(context, job, progress):
            calls.append(1)
            raise JobFailed("nothing to do", {"results": ["x"]})

        job, _ = await enqueue(JobModel(db))
        worker, task = start_worker(db, {"t": handler})
        await wait_until(lambda: _is(db, job["_id"], FAILED))
        await worker.stop()
        await task
        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert calls == [1] and stored["error"] == "nothing to do" and stored["result"] == {"results": ["x"]}

    run_with_db(scenario)


@needs_mongo
def test_concurrency_is_limited_and_two_workers_never_run_the_same_job():
    async def scenario(db):
        running, peak, ran = 0, 0, []

        async def handler(context, job, progress):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            ran.append(job["project_id"])
            await asyncio.sleep(0.2)
            running -= 1
            return {}

        jobs = JobModel(db)
        ids = [(await enqueue(jobs, project=f"p{i}"))[0]["_id"] for i in range(6)]
        one, task_one = start_worker(db, {"t": handler}, SimpleNamespace(**{**vars(SETTINGS), "JOB_CONCURRENCY": 2}), "a")
        two, task_two = start_worker(db, {"t": handler}, SimpleNamespace(**{**vars(SETTINGS), "JOB_CONCURRENCY": 2}), "b")

        async def all_done():
            return all([await status_of(db, i) == SUCCEEDED for i in ids])

        await wait_until(all_done)
        await asyncio.gather(one.stop(), two.stop(), task_one, task_two)
        assert sorted(ran) == [f"p{i}" for i in range(6)]   # each exactly once
        assert peak <= 4                                     # two workers x concurrency 2

    run_with_db(scenario)


@needs_mongo
def test_a_worker_that_lost_its_job_stops_working_on_it_and_does_not_overwrite_the_new_owner():
    async def scenario(db):
        cancelled = asyncio.Event()
        started = asyncio.Event()

        async def handler(context, job, progress):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        job, _ = await enqueue(JobModel(db))
        settings = SimpleNamespace(**{**vars(SETTINGS), "JOB_LEASE_SECONDS": 3})   # renews every second
        worker, task = start_worker(db, {"t": handler}, settings)
        await asyncio.wait_for(started.wait(), 5)

        await db["jobs"].update_one({"_id": job["_id"]}, {"$set": {"worker_id": "someone-else"}})
        await asyncio.wait_for(cancelled.wait(), 5)

        stored = await db["jobs"].find_one({"_id": job["_id"]})
        assert stored["worker_id"] == "someone-else" and stored["status"] == RUNNING
        await worker.stop()
        await task

    run_with_db(scenario)


@needs_mongo
def test_stopping_the_worker_lets_a_running_job_finish():
    async def scenario(db):
        started = asyncio.Event()

        async def handler(context, job, progress):
            started.set()
            await asyncio.sleep(0.3)
            return {"finished": True}

        job, _ = await enqueue(JobModel(db))
        worker, task = start_worker(db, {"t": handler})
        await asyncio.wait_for(started.wait(), 5)
        await worker.stop()
        await task
        assert await status_of(db, job["_id"]) == SUCCEEDED

    run_with_db(scenario)


# ── routes ───────────────────────────────────────────────────────────────────
USER = {"_id": "u1", "email": "a@example.com"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    original_init = BaseController.__init__

    def patched_init(self):
        original_init(self)
        self.files_dir = str(tmp_path)

    monkeypatch.setattr(BaseController, "__init__", patched_init)

    app = FastAPI()
    install_error_handlers(app)
    for router in (data_routes.data_router, jobs_routes.jobs_router, nlp_routes.nlp_router):
        app.include_router(router)
    current = {"user": USER}
    app.dependency_overrides[get_current_user] = lambda: current["user"]
    db_name = f"test_jobs_{uuid.uuid4().hex[:8]}"

    @app.on_event("startup")
    async def start():
        conn = AsyncIOMotorClient(MONGO_URL)
        app.state.services = SimpleNamespace(mongo_conn=conn, mongo_db=conn[db_name])
        await ensure_all_indexes(app.state.services.mongo_db)

    @app.on_event("shutdown")
    async def stop():
        await app.state.services.mongo_conn.drop_database(db_name)
        app.state.services.mongo_conn.close()

    with TestClient(app) as test_client:
        test_client.current = current
        test_client.db = lambda: app.state.services.mongo_db
        yield test_client


def upload(client, name, content, content_type="text/plain"):
    response = client.post("/api/v1/data/upload/default", files={"file": (name, io.BytesIO(content), content_type)})
    assert response.status_code == 200, response.text
    return response.json()["file_id"]


def work_all(client):
    """Run a real worker (the real handlers) until the queue is empty."""
    async def drain():
        db = client.db()
        worker = JobWorker(JobContext(db, None, None, None, None), db, SETTINGS, handlers=HANDLERS, worker_id="test")
        task = asyncio.create_task(worker.run())

        async def idle():
            return await db["jobs"].count_documents({"status": {"$in": [QUEUED, RUNNING]}}) == 0

        await wait_until(idle)
        await worker.stop()
        await task

    client.portal.call(drain)


@needs_mongo
def test_processing_in_the_background_answers_202_and_the_worker_does_the_work(client):
    upload(client, "notes.txt", b"Some reasonably long text. " * 40)

    response = client.post("/api/v1/data/process/default",
                           json={"chunk_size": 50, "overlap_size": 5, "background": True})
    assert response.status_code == 202
    queued = response.json()
    assert queued["already_queued"] is False and queued["job"]["status"] == QUEUED
    assert client.db and client.portal.call(lambda: client.db()["chunks"].count_documents({})) == 0  # nothing done yet

    work_all(client)

    job = client.get(f"/api/v1/jobs/{queued['job']['job_id']}").json()
    assert job["status"] == SUCCEEDED and job["type"] == PROCESS_DOCUMENTS and job["project"] == "default"
    assert job["result"]["processed_files"] == 1 and job["progress"] == {"done": 1, "total": 1}
    assert client.portal.call(lambda: client.db()["chunks"].count_documents({})) == job["result"]["inserted_chunks"] > 1


@needs_mongo
def test_asking_twice_returns_the_job_already_waiting(client):
    upload(client, "notes.txt", b"Some reasonably long text. " * 40)
    body = {"chunk_size": 50, "overlap_size": 5, "background": True}
    first = client.post("/api/v1/data/process/default", json=body).json()
    second = client.post("/api/v1/data/process/default", json=body).json()
    assert second["already_queued"] is True and second["job"]["job_id"] == first["job"]["job_id"]
    assert len(client.get("/api/v1/jobs").json()["jobs"]) == 1


@needs_mongo
def test_a_bad_request_is_rejected_at_once_and_queues_nothing(client):
    nothing = client.post("/api/v1/data/process/default", json={"background": True})
    unknown = client.post("/api/v1/data/process/default", json={"file_id": "ghost.txt", "background": True})
    assert nothing.status_code == 400 and unknown.status_code == 400
    assert client.get("/api/v1/jobs").json()["jobs"] == []


@needs_mongo
def test_a_job_whose_work_cannot_succeed_ends_failed_with_its_per_file_results(client):
    upload(client, "scan.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n", "application/pdf")
    queued = client.post("/api/v1/data/process/default", json={"background": True}).json()["job"]
    work_all(client)

    job = client.get(f"/api/v1/jobs/{queued['job_id']}").json()
    assert job["status"] == FAILED and job["attempts"] == 1          # not retried: the file will not change
    assert job["result"]["results"][0]["status"] == "failed"


@needs_mongo
def test_indexing_can_be_queued_too(client):
    response = client.post("/api/v1/nlp/index/push/default", json={"do_reset": True, "background": True})
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["type"] == "index_documents" and job["project"] == "default"


@needs_mongo
def test_job_routes_show_only_the_callers_jobs(client):
    upload(client, "notes.txt", b"Some reasonably long text. " * 40)
    job_id = client.post("/api/v1/data/process/default", json={"background": True}).json()["job"]["job_id"]

    client.current["user"] = {"_id": "someone-else", "email": "b@example.com"}
    assert client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert client.get("/api/v1/jobs").json()["jobs"] == []
    assert client.get("/api/v1/jobs/not-an-id").status_code == 404


@needs_mongo
def test_without_background_the_routes_still_work_inline(client):
    upload(client, "notes.txt", b"Some reasonably long text. " * 40)
    response = client.post("/api/v1/data/process/default", json={"chunk_size": 50, "overlap_size": 5})
    assert response.status_code == 200 and response.json()["processed_files"] == 1
    assert client.get("/api/v1/jobs").json()["jobs"] == []
