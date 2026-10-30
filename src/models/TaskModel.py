from models.BaseDataModel import BaseDataModel
from models.db_schemes.task import Task
import logging

logger = logging.getLogger('uvicorn.error')

class TaskModel(BaseDataModel):
    
    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.tasks
        self.scans = self.db_client.task_email_scans  # emails already mined for tasks

    @classmethod
    async def create_instance(cls, db_client):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        # Create indexes
        await self.collection.create_index([("project_id", 1)])
        await self.collection.create_index([("status", 1)])
        await self.collection.create_index([("priority", 1)])
        await self.collection.create_index([("deadline", 1)])
        await self.collection.create_index([("user_id", 1), ("project_id", 1), ("description_key", 1)])
        await self.scans.create_index([("user_id", 1), ("message_id", 1)], unique=True)

    async def create_task(self, task: Task) -> bool:
        try:
            task_dict = task.dict(by_alias=True, exclude={"task_id"}, exclude_none=True)
            result = await self.collection.insert_one(task_dict)
            return result.acknowledged
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return False

    @staticmethod
    def make_description_key(description: str) -> str:
        return " ".join((description or "").lower().split())

    async def create_task_if_new(self, task: Task) -> bool:
        """Insert unless the user already has a pending task with the same description."""
        task.description_key = self.make_description_key(task.description)
        existing = await self.collection.find_one({
            "user_id": task.user_id,
            "project_id": task.project_id,
            "description_key": task.description_key,
            "status": "pending",
        })
        if existing:
            return False
        return await self.create_task(task)

    async def filter_unscanned_emails(self, user_id: str, message_ids: list[str]) -> list[str]:
        """Return the message ids that have not been mined for tasks yet."""
        if not message_ids:
            return []
        seen = {
            doc["message_id"]
            async for doc in self.scans.find({"user_id": user_id, "message_id": {"$in": message_ids}}, {"message_id": 1})
        }
        return [m for m in message_ids if m not in seen]

    async def mark_emails_scanned(self, user_id: str, message_ids: list[str]) -> None:
        for message_id in message_ids:
            await self.scans.update_one(
                {"user_id": user_id, "message_id": message_id},
                {"$setOnInsert": {"user_id": user_id, "message_id": message_id}},
                upsert=True,
            )

    async def get_pending_tasks(self, project_id: str, user_id: str, limit: int = 10) -> list[Task]:
        try:
            cursor = self.collection.find({"project_id": project_id, "user_id": user_id, "status": "pending"})
            cursor = cursor.sort("created_at", -1).limit(limit)
            documents = await cursor.to_list(length=limit)
            
            tasks = []
            for doc in documents:
                doc["_id"] = str(doc["_id"])
                tasks.append(Task(**doc))
                
            return tasks
        except Exception as e:
            logger.error(f"Error fetching tasks: {e}")
            return []

    async def search_tasks(self, project_id: str, user_id: str, text: str, limit: int = 10) -> list[Task]:
        try:
            cursor = self.collection.find(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "status": "pending",
                    "$text": {"$search": text},
                },
                {"score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"}), ("created_at", -1)]).limit(limit)
            documents = await cursor.to_list(length=limit)
        except Exception as e:
            import logging
            logging.getLogger("uvicorn.error").error("Failed to create TTL index for TaskModel", exc_info=True)
            cursor = self.collection.find(
                {
                    "project_id": project_id,
                    "status": "pending",
                    "description": {"$regex": text, "$options": "i"},
                }
            ).sort("created_at", -1).limit(limit)
            documents = await cursor.to_list(length=limit)

        tasks = []
        for doc in documents:
            doc["_id"] = str(doc["_id"])
            tasks.append(Task(**doc))
        return tasks
