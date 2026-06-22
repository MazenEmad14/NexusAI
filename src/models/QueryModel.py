from .BaseDataModel import BaseDataModel
from .db_schemes import Query
from .enums.DataBaseEnum import DataBaseEnum

class QueryModel(BaseDataModel):

    def __init__(self, db_client: object):
        super().__init__(db_client=db_client)
        self.collection = self.db_client[DataBaseEnum.COLLECTION_QUERY_NAME.value]

    @classmethod
    async def create_instance(cls, db_client: object):
        instance = cls(db_client)
        await instance.init_collection()
        return instance

    async def init_collection(self):
        all_collections = await self.db_client.list_collection_names()
        if DataBaseEnum.COLLECTION_QUERY_NAME.value not in all_collections:
            self.collection = self.db_client[DataBaseEnum.COLLECTION_QUERY_NAME.value]
            indexes = Query.get_indexes()
            for index in indexes:
                await self.collection.create_index(
                    index["key"],
                    name=index["name"],
                    unique=index["unique"]
                )

    async def create_query(self, query: Query):
        result = await self.collection.insert_one(
            query.dict(by_alias=True, exclude_unset=True)
        )
        query.id = result.inserted_id
        return query

    async def update_query_status(self, query_id, status: str, 
                                   response: str = "", 
                                   request_type: str = "",
                                   action_type: str = ""):
        update_data = {"status": status}
        if response:
            update_data["response"] = response
        if request_type:
            update_data["request_type"] = request_type
        if action_type:
            update_data["action_type"] = action_type

        await self.collection.update_one(
            {"_id": query_id},
            {"$set": update_data}
        )

    async def get_recent_queries(self, project_id: str, limit: int = 10):
        cursor = self.collection.find(
            {"project_id": project_id}
        ).sort("created_at", -1).limit(limit)

        queries = []
        async for doc in cursor:
            queries.append(Query(**doc))
        return queries
