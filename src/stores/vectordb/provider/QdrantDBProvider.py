from ..VectorDBEnums import VectorDBEnums, DistanceMethodEnums
from ..VectorDBInerface import VectorDBInterface
from qdrant_client import models, QdrantClient
import logging
from typing import List
import uuid
from models.db_schemes import RetrievedDocument

class QdrantDBProvider(VectorDBInterface):
    def __init__(self, db_path: str, distance_method: str):
        self.client = None
        self.db_path = db_path
        self.distance_method = models.Distance.COSINE

        if distance_method == DistanceMethodEnums.COSINE:
            self.distance_method = models.Distance.COSINE
        elif distance_method == DistanceMethodEnums.DOT:
            self.distance_method = models.Distance.DOT

        self.logger = logging.getLogger(__name__)

    def connect(self):
        self.client = QdrantClient(path=self.db_path)

    def disconnect(self):
        self.client = None

    def is_collection_existed(self, collection_name: str) -> bool:
        return self.client.collection_exists(collection_name=collection_name)

    def list_all_collections(self) -> List:
        return self.client.get_collections()

    def get_collection_info(self, collection_name: str) -> dict:
        return self.client.get_collection(collection_name=collection_name)

    def delete_collection(self, collection_name: str):
        if self.is_collection_existed(collection_name):
            return self.client.delete_collection(collection_name=collection_name)

    def create_collection(
        self,
        collection_name: str,
        embedding_size: int,
        do_reset: bool = False,
    ):
        if do_reset:
            _ = self.delete_collection(collection_name=collection_name)

        if not self.is_collection_existed(collection_name):
            _ = self.client.create_collection(
                collection_name=collection_name,
                vectors_config=models.VectorParams(
                    size=embedding_size,
                    distance=self.distance_method,
                ),
            )
            return True

        return False

    # -----------------------------
    # INSERT ONE
    # -----------------------------
    def insert_one(
        self,
        collection_name: str,
        text: str,
        vector: list,
        metadata: dict = None,
        record_id: str = None,
    ):
        if not self.is_collection_existed(collection_name):
            self.logger.error(
                f"Can not insert new record to non-existed collection: {collection_name}"
            )
            return False

        # ✅ Generate ID if not provided
        if record_id is None:
            record_id = str(uuid.uuid4())

        record_kwargs = {
            "id": record_id,
            "vector": vector,
            "payload": {
                "text": text,
                "metadata": metadata,
            },
        }

        try:
            _ = self.client.upload_records(
                collection_name=collection_name,
                records=[models.Record(**record_kwargs)],
            )
        except Exception as e:
            self.logger.error(f"Error while inserting record: {e}")
            return False

        return True

    # -----------------------------
    # INSERT MANY
    # -----------------------------
    def insert_many(
        self,
        collection_name: str,
        texts: list,
        vectors: list,
        metadata: list = None,
        record_ids: list = None,
        batch_size: int = 50,
    ):
        if metadata is None:
            metadata = [None] * len(texts)

        if record_ids is None:
            record_ids = [None] * len(texts)

        for i in range(0, len(texts), batch_size):
            batch_end = i + batch_size

            batch_texts = texts[i:batch_end]
            batch_vectors = vectors[i:batch_end]
            batch_metadata = metadata[i:batch_end]

            batch_records = []

            for x in range(len(batch_texts)):
                record_id = record_ids[i + x]

                # ✅ Generate ID if not provided
                if record_id is None:
                    record_id = str(uuid.uuid4())

                record_kwargs = {
                    "id": record_id,
                    "vector": batch_vectors[x],
                    "payload": {
                        "text": batch_texts[x],
                        "metadata": batch_metadata[x],
                    },
                }

                batch_records.append(models.Record(**record_kwargs))

            try:
                _ = self.client.upload_records(
                    collection_name=collection_name,
                    records=batch_records,
                )
            except Exception as e:
                self.logger.error(f"Error while inserting batch: {e}")
                return False

        return True

    # -----------------------------
    # SEARCH
    # -----------------------------
    def search_by_vector(
        self,
        collection_name: str,
        vector: list,
        limit: int = 5,
    ):
        if not self.is_collection_existed(collection_name=collection_name):
            self.logger.error(f"Can not search non-existed collection: {collection_name}")
            return False

        result = self.client.search(
            collection_name=collection_name,
            query_vector=vector,
            limit=limit,
        )
        if not result or len(result) == 0:
            return None
        return [
            RetrievedDocument(**{
                "score": res.score,
                "text": res.payload["text"]
            })
            for res in result
        ]
