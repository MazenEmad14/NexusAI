from datetime import datetime, timedelta
from typing import Optional

from pymongo import ReturnDocument

from models.BaseDataModel import BaseDataModel


class RateLimitModel(BaseDataModel):
    """Shared (MongoDB-backed) counters for rate limiting, so limits hold across workers and restarts.

    Each key counts events inside a fixed window that starts at the first event. MongoDB's TTL
    index removes expired counters; reads also check the expiry because the TTL sweep runs only
    about once a minute.
    """

    COLLECTION_NAME = "rate_limits"

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [{"key": [("expires_at", 1)], "name": "rate_limit_expiry", "options": {"expireAfterSeconds": 0}}]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def status(self, key: str) -> tuple[int, Optional[datetime]]:
        """(events in the current window, when the window ends); (0, None) if there is none."""
        doc = await self.collection.find_one({"_id": key, "expires_at": {"$gt": datetime.utcnow()}})
        return (doc["count"], doc["expires_at"]) if doc else (0, None)

    async def hit(self, key: str, window_seconds: int) -> int:
        """Record one event and return the count in the current window."""
        now = datetime.utcnow()
        doc = await self.collection.find_one_and_update(
            {"_id": key, "expires_at": {"$gt": now}},
            {"$inc": {"count": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if doc:
            return doc["count"]
        await self.collection.replace_one(
            {"_id": key},
            {"_id": key, "count": 1, "expires_at": now + timedelta(seconds=window_seconds)},
            upsert=True,
        )
        return 1

    async def reset(self, key: str) -> None:
        await self.collection.delete_one({"_id": key})
