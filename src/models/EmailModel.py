from .BaseDataModel import BaseDataModel
from .db_schemes import Email
from .enums.DataBaseEnum import DataBaseEnum

class EmailModel(BaseDataModel):

    def __init__(self, db_client: object):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[DataBaseEnum.COLLECTION_EMAIL_NAME.value]

    @classmethod
    async def create_instance(cls, db_client: object):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        all_collections = await self.db_client.list_collection_names()
        if DataBaseEnum.COLLECTION_EMAIL_NAME.value not in all_collections:
            self.collection = self.db_client[DataBaseEnum.COLLECTION_EMAIL_NAME.value]
            indexes = Email.get_indexes()
            for index in indexes:
                await self.collection.create_index(
                    index["key"],
                    name=index["name"],
                    unique=index["unique"]
                )

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
