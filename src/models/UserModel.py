from models.BaseDataModel import BaseDataModel
from models.db_schemes.user import User
from typing import Optional, Dict, Any
import logging
from helpers.token_crypto import encrypt_token, decrypt_token


def _normalize(email: str) -> str:
    return (email or '').strip().lower()

logger = logging.getLogger('uvicorn.error')

class UserModel(BaseDataModel):
    
    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.users

    COLLECTION_NAME = "users"

    @classmethod
    def index_specs(cls):
        return [{"key": [("email", 1)], "name": "email_1", "unique": True}]

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

    async def create_user(self, user: User) -> bool:
        try:
            user_dict = user.dict(by_alias=True, exclude={"user_id"}, exclude_none=True)
            user_dict["email"] = _normalize(user_dict["email"])
            result = await self.collection.insert_one(user_dict)
            return result.acknowledged
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return False

    async def get_user_by_email(self, email: str) -> dict:
        try:
            user = await self.collection.find_one({"email": _normalize(email)})
            return user
        except Exception as e:
            logger.error(f"Error fetching user by email: {e}")
            return None

    async def update_google_auth_status(self, email: str, status: bool) -> bool:
        try:
            result = await self.collection.update_one(
                {"email": email},
                {"$set": {"is_google_connected": status}}
            )
            return result.matched_count > 0  # a no-op update (value unchanged) is still a success
        except Exception as e:
            logger.error(f"Error updating Google Auth status: {e}")
            return False

    async def update_google_token(self, email: str, token_dict: Optional[Dict[str, Any]]) -> bool:
        """Save per-user Google OAuth token dict to MongoDB."""
        try:
            result = await self.collection.update_one(
                {"email": email},
                {"$set": {
                    "google_token": encrypt_token(token_dict),
                    "is_google_connected": token_dict is not None
                }}
            )
            return result.matched_count > 0
        except Exception as e:
            logger.error(f"Error updating Google token for {email}: {e}")
            return False

    async def get_google_token(self, email: str) -> Optional[Dict[str, Any]]:
        """Retrieve per-user Google OAuth token dict from MongoDB."""
        try:
            user = await self.collection.find_one({"email": email}, {"google_token": 1})
            return decrypt_token(user.get("google_token")) if user else None
        except Exception as e:
            logger.error(f"Error fetching Google token for {email}: {e}")
            return None

    async def list_google_users(self) -> list[Dict[str, Any]]:
        """Users who connected Google, with their decrypted token (for background jobs)."""
        try:
            cursor = self.collection.find(
                {"google_token": {"$exists": True, "$ne": None}},
                {"email": 1, "google_token": 1},
            )
            users = []
            async for doc in cursor:
                token = decrypt_token(doc.get("google_token"))
                if token:
                    users.append({"user_id": str(doc["_id"]), "email": doc["email"], "google_token": token})
            return users
        except Exception as e:
            logger.error(f"Error listing Google-connected users: {e}")
            return []

    async def normalize_existing_emails(self) -> int:
        """Lower-case emails stored by older versions (which kept the case the user typed).

        An address whose lower-cased form already belongs to another account is left alone and
        reported, because merging two accounts is not something to do automatically.
        """
        changed = 0
        async for doc in self.collection.find({"email": {"$regex": "[A-Z]"}}, {"email": 1}):
            lowered = _normalize(doc["email"])
            if await self.collection.find_one({"email": lowered}, {"_id": 1}):
                logger.warning(f"Two accounts share the email {lowered} apart from letter case; left as is")
                continue
            await self.collection.update_one({"_id": doc["_id"]}, {"$set": {"email": lowered}})
            changed += 1
        if changed:
            logger.info(f"Lower-cased {changed} stored user emails")
        return changed
