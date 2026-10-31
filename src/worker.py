"""Run a background job worker as its own process:  python worker.py

Use it with JOB_WORKER_MODE=external on the API, so the heavy work (reading PDFs, embedding) does not share
a process with the web server. Start as many as you need; they share the queue in MongoDB safely.
"""
import asyncio
import logging
import signal
import sys
from contextlib import AsyncExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from motor.motor_asyncio import AsyncIOMotorClient

from helpers.clients import open_clients
from helpers.config import get_settings
from helpers.logging_setup import configure_logging
from models.indexes import ensure_all_indexes
from services.jobs import JobContext, JobWorker

logger = logging.getLogger("uvicorn.error")


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.LOG_FORMAT)
    settings.ensure_valid()

    async with AsyncExitStack() as stack:
        mongo_conn = AsyncIOMotorClient(settings.MONGODB_URL)
        stack.callback(mongo_conn.close)
        db = mongo_conn[settings.MONGODB_DATABASE]
        await ensure_all_indexes(db)

        generation, embedding, vectordb, templates = open_clients(settings, stack)
        worker = JobWorker(JobContext(db, generation, embedding, vectordb, templates), db, settings)

        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            try:
                loop.add_signal_handler(getattr(signal, name), lambda: asyncio.create_task(worker.stop()))
            except (NotImplementedError, AttributeError):
                pass  # Windows: Ctrl+C raises KeyboardInterrupt below instead

        try:
            await worker.run()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
