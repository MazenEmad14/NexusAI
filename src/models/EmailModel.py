from .BaseDataModel import BaseDataModel
from .db_schemes import Email
from .enums.DataBaseEnum import DataBaseEnum

class EmailModel(BaseDataModel):

    def __init__(self, db_client: object):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[DataBaseEnum.COLLECTION_EMAIL_NAME.value]

    COLLECTION_NAME = DataBaseEnum.COLLECTION_EMAIL_NAME.value

    @classmethod
    def index_specs(cls):
        return Email.get_indexes()

    @classmethod
    async def create_instance(cls, db_client: object):
        instance = cls(db_client)
        return instance

    async def log_email(self, email: Email):
        result = await self.collection.insert_one(
            email.dict(by_alias=True, exclude_unset=True)
        )
        email.id = result.inserted_id
        return email

    async def get_emails_by_project(self, project_id: str, limit: int = 20):
        cursor = self.collection.find(
            {"project_id": project_id}
        ).sort("created_at", -1).limit(limit)

        emails = []
        async for doc in cursor:
            emails.append(Email(**doc))
        return emails
