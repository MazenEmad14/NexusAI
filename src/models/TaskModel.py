from stores.db.providers.motor.MotorProvider import MotorProvider
from models.BaseDataModel import BaseDataModel
from models.db_schemes.task import Task
import logging

logger = logging.getLogger('uvicorn.error')

class TaskModel(BaseDataModel):
    
    def __init__(self, db_client: MotorProvider):
        super().__init__(db_client=db_client)
        self.collection = self.db_client.db.tasks

    @classmethod
    async def create_instance(cls, db_client: MotorProvider):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        # Create indexes
        await self.collection.create_index([("project_id", 1)])
        await self.collection.create_index([("status", 1)])

    async def create_task(self, task: Task) -> bool:
        try:
            task_dict = task.dict(by_alias=True, exclude={"task_id"})
            result = await self.collection.insert_one(task_dict)
            return result.acknowledged
        except Exception as e:
            logger.error(f"Error creating task: {e}")
            return False

    async def get_pending_tasks(self, project_id: str, limit: int = 10) -> list[Task]:
        try:
            cursor = self.collection.find({"project_id": project_id, "status": "pending"})
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
