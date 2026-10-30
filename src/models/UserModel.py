from models.BaseDataModel import BaseDataModel
from models.db_schemes.user import User
from typing import Optional, Dict, Any
import logging
from helpers.token_crypto import encrypt_token, decrypt_token

logger = logging.getLogger('uvicorn.error')

class UserModel(BaseDataModel):
    
    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.users

    @classmethod
    async def create_instance(cls, db_client):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        # Create unique index on email
        await self.collection.create_index([("email", 1)], unique=True)

    async def create_user(self, user: User) -> bool:
        try:
            user_dict = user.dict(by_alias=True, exclude={"user_id"}, exclude_none=True)
            result = await self.collection.insert_one(user_dict)
            return result.acknowledged
        except Exception as e:
            logger.error(f"Error creating user: {e}")
            return False

    async def get_user_by_email(self, email: str) -> dict:
        try:
            user = await self.collection.find_one({"email": email})
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
            return result.modified_count > 0
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
