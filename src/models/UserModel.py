from models.BaseDataModel import BaseDataModel
from models.db_schemes.user import User
import logging

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
