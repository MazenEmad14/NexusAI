"""Create every collection index once, at startup."""
import logging

from models.AssetModel import AssetModel
from models.ChunkModel import ChunkModel
from models.EmailModel import EmailModel
from models.MeetingModel import MeetingModel
from models.ProjectModel import ProjectModel
from models.QueryModel import QueryModel
from models.RateLimitModel import RateLimitModel
from models.ReminderModel import ReminderModel
from models.SessionModel import SessionModel
from models.TaskModel import TaskModel
from models.UserModel import UserModel

logger = logging.getLogger("uvicorn.error")

ALL_MODELS = (
    ProjectModel, ChunkModel, AssetModel, QueryModel, EmailModel, MeetingModel,
    ReminderModel, TaskModel, UserModel, RateLimitModel, SessionModel,
)


async def ensure_all_indexes(db) -> None:
    for model in ALL_MODELS:
        await model.ensure_indexes(db)
    logger.info("MongoDB indexes ensured")
