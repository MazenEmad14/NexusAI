from datetime import datetime, timedelta
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from models.BaseDataModel import BaseDataModel

QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"

MAX_BACKOFF_SECONDS = 300


class JobModel(BaseDataModel):
    """A queue of background jobs, kept in MongoDB so it needs no extra infrastructure.

    Workers claim a job with one atomic update, so any number of workers (threads, processes, machines)
    can share the queue and no job runs twice at once. A claim is a *lease*: the worker renews it while it
    works, and a job whose lease runs out (the worker died) goes back to the queue, up to `max_attempts`.

    Only one job of a kind per project can be queued or running at a time (`active_key`), so pressing a
    button twice does not process the same files twice.
    """

    COLLECTION_NAME = "jobs"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("status", 1), ("run_after", 1), ("created_at", 1)], "name": "job_claim"},
            {"key": [("status", 1), ("lease_expires_at", 1)], "name": "job_lease"},
            {"key": [("user_id", 1), ("created_at", -1)], "name": "job_user"},
            # Unique only while the job is active: the key is removed when it finishes.
            {"key": [("active_key", 1)], "name": "job_active", "unique": True,
             "options": {"partialFilterExpression": {"active_key": {"$type": "string"}}}},
            {"key": [("expires_at", 1)], "name": "job_expiry", "options": {"expireAfterSeconds": 0}},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    # ── producing ────────────────────────────────────────────────────────────
    async def enqueue(self, job_type: str, user_id: str, project_id: str, project_name: str,
                      payload: dict, max_attempts: int) -> tuple[dict, bool]:
        """Add a job. Returns (job, created); `created` is False when an identical job is already active."""
        now = datetime.utcnow()
        doc = {
            "type": job_type,
            "user_id": user_id,
            "project_id": project_id,
            "project_name": project_name,
            "payload": payload,
            "status": QUEUED,
            "attempts": 0,
            "max_attempts": max_attempts,
            "run_after": now,
            "lease_expires_at": None,
            "worker_id": None,
            "progress": None,
            "result": None,
            "error": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "active_key": f"{job_type}:{project_id}",
        }
        try:
            inserted = await self.collection.insert_one(doc)
        except DuplicateKeyError:
            existing = await self.collection.find_one({"active_key": doc["active_key"]})
            if existing is not None:
                return existing, False
            return await self.enqueue(job_type, user_id, project_id, project_name, payload, max_attempts)
        doc["_id"] = inserted.inserted_id
        return doc, True

    # ── consuming ────────────────────────────────────────────────────────────
    async def give_up_on_exhausted(self, retention_days: int = 7) -> int:
        """Jobs whose worker died on every attempt are failed instead of being retried for ever."""
        now = datetime.utcnow()
        result = await self.collection.update_many(
            {"status": RUNNING, "lease_expires_at": {"$lt": now}, "$expr": {"$gte": ["$attempts", "$max_attempts"]}},
            {"$set": {"status": FAILED, "error": "The job was interrupted too many times.", "finished_at": now,
                      "worker_id": None, "lease_expires_at": None,
                      "expires_at": now + timedelta(days=retention_days)},
             "$unset": {"active_key": ""}},
        )
        return result.modified_count

    async def claim_next(self, worker_id: str, lease_seconds: int, job_types: Optional[list] = None,
                         retention_days: int = 7) -> Optional[dict]:
        """Take the oldest job that is due (or whose worker went silent). None when there is nothing to do."""
        now = datetime.utcnow()
        await self.give_up_on_exhausted(retention_days)
        due = {"$or": [
            {"status": QUEUED, "run_after": {"$lte": now}},
            {"status": RUNNING, "lease_expires_at": {"$lt": now}},
        ]}
        query = {"$and": [due, {"type": {"$in": job_types}}]} if job_types else due
        return await self.collection.find_one_and_update(
            query,
            {"$set": {"status": RUNNING, "worker_id": worker_id, "started_at": now,
                      "lease_expires_at": now + timedelta(seconds=lease_seconds)},
             "$inc": {"attempts": 1}},
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def heartbeat(self, job_id, worker_id: str, lease_seconds: int) -> bool:
        """Renew the lease. False means this worker no longer owns the job (it was reassigned)."""
        result = await self.collection.update_one(
            {"_id": job_id, "worker_id": worker_id, "status": RUNNING},
            {"$set": {"lease_expires_at": datetime.utcnow() + timedelta(seconds=lease_seconds)}},
        )
        return result.modified_count == 1

    async def set_progress(self, job_id, worker_id: str, done: int, total: int) -> None:
        await self.collection.update_one(
            {"_id": job_id, "worker_id": worker_id, "status": RUNNING},
            {"$set": {"progress": {"done": done, "total": total}}},
        )

    async def complete(self, job_id, worker_id: str, result: dict, retention_days: int) -> bool:
        now = datetime.utcnow()
        updated = await self.collection.update_one(
            {"_id": job_id, "worker_id": worker_id, "status": RUNNING},
            {"$set": {"status": SUCCEEDED, "result": result, "error": None, "finished_at": now,
                      "lease_expires_at": None, "expires_at": now + timedelta(days=retention_days)},
             "$unset": {"active_key": ""}},
        )
        return updated.modified_count == 1

    async def fail(self, job_id, worker_id: str, error: str, retry: bool, retention_days: int,
                   result: Optional[dict] = None) -> str:
        """Record a failure. With `retry`, and attempts left, the job is queued again after a growing delay.
        Returns the job's new status ("queued", "failed") or "lost" if this worker no longer owned it."""
        job = await self.collection.find_one({"_id": job_id, "worker_id": worker_id, "status": RUNNING})
        if job is None:
            return "lost"
        now = datetime.utcnow()
        if retry and job["attempts"] < job["max_attempts"]:
            delay = min(5 * 2 ** (job["attempts"] - 1), MAX_BACKOFF_SECONDS)
            await self.collection.update_one(
                {"_id": job_id, "worker_id": worker_id},
                {"$set": {"status": QUEUED, "run_after": now + timedelta(seconds=delay), "error": error,
                          "worker_id": None, "lease_expires_at": None}},
            )
            return QUEUED
        await self.collection.update_one(
            {"_id": job_id, "worker_id": worker_id},
            {"$set": {"status": FAILED, "error": error, "result": result, "finished_at": now,
                      "worker_id": None, "lease_expires_at": None,
                      "expires_at": now + timedelta(days=retention_days)},
             "$unset": {"active_key": ""}},
        )
        return FAILED

    # ── reading ──────────────────────────────────────────────────────────────
    @staticmethod
    def _object_id(job_id: str) -> Optional[ObjectId]:
        try:
            return ObjectId(job_id)
        except (InvalidId, TypeError):
            return None

    async def get_for_user(self, job_id: str, user_id: str) -> Optional[dict]:
        """A user's own job; None for an unknown id or someone else's job (the two look the same)."""
        oid = self._object_id(job_id)
        if oid is None:
            return None
        return await self.collection.find_one({"_id": oid, "user_id": user_id})

    async def list_for_user(self, user_id: str, limit: int = 20) -> list[dict]:
        cursor = self.collection.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
        return [doc async for doc in cursor]

    @staticmethod
    def public(job: dict) -> dict:
        """What the API shows about a job: no internal ids, payload or worker details."""
        def stamp(value):
            return value.isoformat() + "Z" if value else None

        return {
            "job_id": str(job["_id"]),
            "type": job["type"],
            "project": job.get("project_name"),
            "status": job["status"],
            "progress": job.get("progress"),
            "result": job.get("result"),
            "error": job.get("error"),
            "attempts": job.get("attempts", 0),
            "created_at": stamp(job.get("created_at")),
            "finished_at": stamp(job.get("finished_at")),
        }
