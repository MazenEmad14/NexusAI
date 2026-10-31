"""Health endpoints for load balancers, orchestrators and people asking "is it up?".

    GET /health        liveness: the process is running and can answer (touches nothing else)
    GET /health/ready  readiness: it can do its job: MongoDB and the vector store respond, configuration is valid

Both are public and say only ok/error per dependency; the reason goes to the log with the request id.
"""
import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from helpers.config import get_settings

logger = logging.getLogger("uvicorn.error")

health_router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 3.0


async def _check(name: str, awaitable) -> str:
    try:
        await asyncio.wait_for(awaitable, timeout=CHECK_TIMEOUT_SECONDS)
        return "ok"
    except asyncio.TimeoutError:
        logger.warning(f"Health check {name} timed out after {CHECK_TIMEOUT_SECONDS}s")
        return "timeout"
    except Exception:
        logger.warning(f"Health check {name} failed", exc_info=True)
        return "error"


@health_router.get("/health")
async def liveness():
    return {"status": "ok"}


@health_router.get("/health/ready")
async def readiness(request: Request):
    services = getattr(request.app.state, "services", None)
    if services is None:
        return JSONResponse(status_code=503, content={"status": "starting", "checks": {}})

    settings = get_settings()
    mongo, vector = await asyncio.gather(
        _check("mongodb", services.mongo_db.command("ping")),
        _check("vector_db", asyncio.to_thread(services.vectordb_client.list_all_collections)),
    )
    checks = {
        "mongodb": mongo,
        "vector_db": vector,
        # No call to the model provider (it costs money and fails on their outages, not ours):
        # only that the keys and models the chosen backends need are configured.
        "configuration": "ok" if not settings.problems() else "error",
    }
    ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "degraded", "version": settings.APP_VERSION, "checks": checks},
    )
