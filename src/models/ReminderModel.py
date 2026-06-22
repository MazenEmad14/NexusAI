from .BaseDataModel import BaseDataModel
from .db_schemes import Reminder
from .enums.DataBaseEnum import DataBaseEnum

class ReminderModel(BaseDataModel):

    def __init__(self, db_client: object):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[DataBaseEnum.COLLECTION_REMINDER_NAME.value]

    @classmethod
    async def create_instance(cls, db_client: object):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        all_collections = await self.db_client.list_collection_names()
        if DataBaseEnum.COLLECTION_REMINDER_NAME.value not in all_collections:
            self.collection = self.db_client[DataBaseEnum.COLLECTION_REMINDER_NAME.value]
            indexes = Reminder.get_indexes()
            for index in indexes:
                await self.collection.create_index(
                    index["key"],
                    name=index["name"],
                    unique=index["unique"]
                )

    async def create_reminder(self, reminder: Reminder):
        result = await self.collection.insert_one(
            reminder.dict(by_alias=True, exclude_unset=True)
        )
        reminder.id = result.inserted_id
        return reminder

    async def get_active_reminders(self, project_id: str, limit: int = 20):
        cursor = self.collection.find(
            {"project_id": project_id, "is_completed": False}
        ).sort("date", 1).limit(limit)

        reminders = []
        async for doc in cursor:
            reminders.append(Reminder(**doc))
        return reminders

    async def mark_completed(self, reminder_id):
        await self.collection.update_one(
            {"_id": reminder_id},
            {"$set": {"is_completed": True}}
        )

    async def get_all_reminders(self, project_id: str, limit: int = 50):
        cursor = self.collection.find(
            {"project_id": project_id}
        ).sort("created_at", -1).limit(limit)

        reminders = []
        async for doc in cursor:
            reminders.append(Reminder(**doc))
        return reminders
