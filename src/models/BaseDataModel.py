import logging

from helpers.config import get_settings

logger = logging.getLogger("uvicorn.error")


class BaseDataModel:
    """Base for Mongo-backed models.

    Indexes are created once at startup (see models/indexes.py), never per request:
    `create_instance` is a plain constructor with no database round-trip.
    """

    COLLECTION_NAME: str = None

    def __init__(self, db_client: object):
        self.db_client = db_client
        self.app_settings = get_settings()

    @classmethod
    def index_specs(cls) -> list[dict]:
        """Index definitions: {"key": [(field, direction)], "name": optional str, "unique": bool, "options": dict}."""
        return []

    @classmethod
    async def ensure_indexes(cls, db_client) -> None:
        """Create this model's indexes. Safe to run on every startup (identical indexes are no-ops).
        A failing index is logged and skipped so one bad index cannot stop the app."""
        if not cls.COLLECTION_NAME:
            return
        collection = db_client[cls.COLLECTION_NAME]
        for spec in cls.index_specs():
            try:
                options = dict(spec.get("options", {}))
                if spec.get("name"):
                    options["name"] = spec["name"]  # unnamed specs keep MongoDB's default name
                await collection.create_index(spec["key"], unique=spec.get("unique", False), **options)
            except Exception:
                logger.error(f"Could not create index {spec.get('name') or spec['key']} on {cls.COLLECTION_NAME}", exc_info=True)
