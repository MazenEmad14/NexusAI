import asyncio
import logging
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent))

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from motor.motor_asyncio import AsyncIOMotorClient

from agent import AgentDeps, build_agent_graph
from controllers.proactive_agent import evaluate_inbox_and_notify
from helpers.clients import open_clients
from helpers.config import get_settings, unknown_env_keys
from helpers.error_handlers import install_error_handlers
from helpers.logging_setup import RequestContextMiddleware, configure_logging
from helpers.services import Services
from models.UserModel import UserModel
from models.indexes import ensure_all_indexes
from routes import agent as agent_routes, auth, base, dashboard, data, health, jobs, local_auth, nlp
from services.jobs import JobWorker
from services.notification_engine import NotificationEngine
from stores.checkpoint.MongoCheckpointer import MongoCheckpointer

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build every service on startup and release them in reverse order on shutdown.

    The exit stack unwinds whatever was already started even if a later step raises, so a failed
    startup does not leave a database connection, scheduler or storage lock behind.
    """
    settings = get_settings()
    configure_logging(settings.LOG_FORMAT)
    settings.ensure_valid()  # refuse to start with a readable list of what is wrong, not fail on a later request
    for name in unknown_env_keys():
        logger.warning(f"{name} in the env file matches no setting and is ignored (a typo?)")

    async with AsyncExitStack() as stack:
        # 1. MongoDB: agent data, users, checkpoints
        mongo_conn = AsyncIOMotorClient(settings.MONGODB_URL)
        stack.callback(mongo_conn.close)
        mongo_db = mongo_conn[settings.MONGODB_DATABASE]
        await ensure_all_indexes(mongo_db)
        await UserModel(mongo_db).normalize_existing_emails()

        # 3. LLM clients, the vector database and the prompt templates
        generation_client, embedding_client, vectordb_client, template_parser = open_clients(settings, stack)

        # 5. The agent, with a persistent checkpointer so pending approvals and chat history
        # survive restarts.
        checkpointer = MongoCheckpointer(mongo_db)
        await checkpointer.setup()
        agent_graph = build_agent_graph(
            AgentDeps(
                db_client=mongo_db,
                generation_client=generation_client,
                embedding_client=embedding_client,
                vectordb_client=vectordb_client,
                template_parser=template_parser,
            ),
            checkpointer=checkpointer,
        )

        services = Services(
            mongo_conn=mongo_conn,
            mongo_db=mongo_db,
            generation_client=generation_client,
            embedding_client=embedding_client,
            vectordb_client=vectordb_client,
            template_parser=template_parser,
            checkpointer=checkpointer,
            agent_graph=agent_graph,
        )

        # 6. Background work
        services.notification_engine = NotificationEngine(services)
        services.notification_engine.start()
        stack.push_async_callback(services.notification_engine.stop)

        services.scheduler = AsyncIOScheduler()
        services.scheduler.add_job(
            evaluate_inbox_and_notify, "interval", minutes=settings.INBOX_CHECK_INTERVAL_MINUTES, args=[services])
        services.scheduler.add_job(
            checkpointer.purge_stale_threads, "interval", hours=24, args=[settings.THREAD_RETENTION_DAYS])
        services.scheduler.start()
        stack.callback(services.scheduler.shutdown)

        # The job queue worker (document processing, indexing). With JOB_WORKER_MODE=external, run worker.py instead.
        if settings.JOB_WORKER_MODE == "embedded":
            worker = JobWorker(services, mongo_db, settings)
            worker_task = asyncio.create_task(worker.run())
            stack.push_async_callback(worker.stop)
            stack.callback(worker_task.cancel)

        # Requests are only served once everything above exists.
        app.state.services = services
        try:
            yield
        finally:
            app.state.services = None


app = FastAPI(lifespan=lifespan)
install_error_handlers(app)

# ── CORS Middleware ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],  # lets the frontend show it when something fails
)
# Added after CORS so it is the outermost layer: every response, preflights included, carries a request id.
app.add_middleware(RequestContextMiddleware)

app.include_router(health.health_router)
app.include_router(base.base_router)
app.include_router(auth.auth_router)
app.include_router(local_auth.local_auth_router)
app.include_router(data.data_router)
app.include_router(jobs.jobs_router)
app.include_router(nlp.nlp_router)
app.include_router(agent_routes.agent_router)
app.include_router(dashboard.dashboard_router)
