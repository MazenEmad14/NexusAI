from datetime import datetime, timedelta
import hashlib
import secrets
import uuid
from typing import Optional

from pymongo import ReturnDocument

from models.BaseDataModel import BaseDataModel


def hash_token(raw: str) -> str:
    """Only the hash is stored, so a copy of the database cannot be used to take over a session."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SessionModel(BaseDataModel):
    """Refresh tokens. Each is single-use: using one issues the next, in the same "family".

    A token that is presented again after it was used is a sign it was copied, so the whole family is
    revoked and the user must sign in again. A second use within a few seconds is let through, because two
    browser tabs refreshing at the same moment is normal and must not log anyone out.
    """

    COLLECTION_NAME = "sessions"
    GRACE_SECONDS = 10

    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[self.COLLECTION_NAME]

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("token_hash", 1)], "name": "session_token_hash", "unique": True},
            {"key": [("family_id", 1)], "name": "session_family"},
            {"key": [("user_id", 1)], "name": "session_user"},
            {"key": [("expires_at", 1)], "name": "session_expiry", "options": {"expireAfterSeconds": 0}},
        ]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def start(self, user_id: str, days: int, family_id: Optional[str] = None) -> str:
        """Create a refresh token (a new family unless one is given) and return the raw value."""
        raw = secrets.token_urlsafe(48)
        now = datetime.utcnow()
        await self.collection.insert_one({
            "token_hash": hash_token(raw),
            "family_id": family_id or uuid.uuid4().hex,
            "user_id": user_id,
            "used": False,
            "used_at": None,
            "created_at": now,
            "expires_at": now + timedelta(days=days),
        })
        return raw

    async def rotate(self, raw: str, days: int) -> Optional[tuple[str, str]]:
        """Exchange a refresh token for the next one: (user_id, new_raw_token), or None if it is not valid."""
        if not raw:
            return None
        now = datetime.utcnow()
        digest = hash_token(raw)

        # Atomic: of two simultaneous requests with the same token only one gets the document back.
        doc = await self.collection.find_one_and_update(
            {"token_hash": digest, "used": False, "expires_at": {"$gt": now}},
            {"$set": {"used": True, "used_at": now}},
            return_document=ReturnDocument.BEFORE,
        )
        if doc is None:
            doc = await self.collection.find_one({"token_hash": digest, "expires_at": {"$gt": now}})
            if doc is None:
                return None  # unknown or expired
            within_grace = doc.get("used_at") and (now - doc["used_at"]).total_seconds() <= self.GRACE_SECONDS
            if not within_grace:
                await self.revoke_family(doc["family_id"])  # an old token came back: assume it was copied
                return None

        return doc["user_id"], await self.start(doc["user_id"], days, family_id=doc["family_id"])

    async def revoke_by_token(self, raw: str) -> None:
        doc = await self.collection.find_one({"token_hash": hash_token(raw or "")}, {"family_id": 1})
        if doc:
            await self.revoke_family(doc["family_id"])

    async def revoke_family(self, family_id: str) -> None:
        await self.collection.delete_many({"family_id": family_id})

    async def revoke_all_for_user(self, user_id: str) -> None:
        await self.collection.delete_many({"user_id": user_id})
