import re

from pymongo.errors import OperationFailure

from models.BaseDataModel import BaseDataModel
from models.db_schemes.task import Task
import logging

logger = logging.getLogger('uvicorn.error')

_INDEX_NOT_FOUND = 27  # MongoDB error code for a $text query without a text index

class TaskModel(BaseDataModel):
    
    def __init__(self, db_client):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.tasks
        self.scans = self.db_client.task_email_scans  # emails already mined for tasks

    COLLECTION_NAME = "tasks"

    @classmethod
    def index_specs(cls):
        return [
            {"key": [("user_id", 1), ("project_id", 1), ("status", 1)]},
            {"key": [("user_id", 1), ("project_id", 1), ("description_key", 1)]},
            {"key": [("deadline", 1)]},
            # Powers search_tasks. Only one text index is allowed per collection.
            {"key": [("description", "text")], "name": "task_description_text"},
        ]

    @classmethod
    async def ensure_indexes(cls, db_client) -> None:
        await super().ensure_indexes(db_client)
        try:
            await db_client.task_email_scans.create_index(
                [("user_id", 1), ("message_id", 1)], unique=True
            )
        except Exception:
            logger.error("Could not create index on task_email_scans", exc_info=True)

    @classmethod
    async def create_instance(cls, db_client):
        return cls(db_client)

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
        """Pending tasks, newest first. Database errors propagate: an empty list must mean "no tasks"."""
        cursor = self.collection.find({"project_id": project_id, "user_id": user_id, "status": "pending"})
        cursor = cursor.sort("created_at", -1).limit(limit)
        documents = await cursor.to_list(length=limit)

        tasks = []
        for doc in documents:
            doc["_id"] = str(doc["_id"])
            tasks.append(Task(**doc))

        return tasks

    async def search_tasks(self, project_id: str, user_id: str, text: str, limit: int = 10) -> list[Task]:
        """Pending tasks of this user matching the text, best matches first."""
        base = {"project_id": project_id, "user_id": user_id, "status": "pending"}
        try:
            cursor = self.collection.find(
                {**base, "$text": {"$search": text}},
                {"score": {"$meta": "textScore"}},
            ).sort([("score", {"$meta": "textScore"}), ("created_at", -1)]).limit(limit)
            documents = await cursor.to_list(length=limit)
        except OperationFailure as exc:
            if exc.code != _INDEX_NOT_FOUND:
                raise
            # The text index does not exist yet (it is created at startup). Match on the
            # individual words instead, escaped so user input can never act as a regex.
            logger.warning("Task text index missing; falling back to keyword matching")
            words = [w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 2][:5]
            if not words:
                return []
            cursor = self.collection.find(
                {**base, "$or": [{"description": {"$regex": re.escape(w), "$options": "i"}} for w in words]}
            ).sort("created_at", -1).limit(limit)
            documents = await cursor.to_list(length=limit)

        tasks = []
        for doc in documents:
            doc["_id"] = str(doc["_id"])
            tasks.append(Task(**doc))
        return tasks
