from .BaseDataModel import BaseDataModel
from .db_schemes import Meeting
from .enums.DataBaseEnum import DataBaseEnum

class MeetingModel(BaseDataModel):

    def __init__(self, db_client: object):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[DataBaseEnum.COLLECTION_MEETING_NAME.value]

    COLLECTION_NAME = DataBaseEnum.COLLECTION_MEETING_NAME.value

    @classmethod
    def index_specs(cls):
        return Meeting.get_indexes()

    @classmethod
    async def create_instance(cls, db_client: object):
        instance = cls(db_client)
        return instance

    async def create_meeting(self, meeting: Meeting):
        result = await self.collection.insert_one(
            meeting.dict(by_alias=True, exclude_unset=True)
        )
        meeting.id = result.inserted_id
        return meeting

    async def get_meetings_by_project(self, project_id: str, limit: int = 20):
        cursor = self.collection.find(
            {"project_id": project_id}
        ).sort("date", -1).limit(limit)

        meetings = []
        async for doc in cursor:
            meetings.append(Meeting(**doc))
        return meetings

    async def update_meeting_status(self, meeting_id, status: str, google_event_id: str = ""):
        update_data = {"status": status}
        if google_event_id:
            update_data["google_event_id"] = google_event_id

        await self.collection.update_one(
            {"_id": meeting_id},
            {"$set": update_data}
        )
