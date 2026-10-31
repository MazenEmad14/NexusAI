"""The services the application builds at startup, in one typed container.

main.py's lifespan creates a `Services` object and stores it as `app.state.services`; request
handlers get it with `services_of(request)` (or the `get_services` dependency). That replaces loose
attributes on the app object, which were untyped and needed `hasattr` checks everywhere.
"""
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException, Request, status


@dataclass
class Services:
    mongo_conn: Any            # motor client
    mongo_db: Any              # motor database (settings.MONGODB_DATABASE)
    generation_client: Any     # LLM provider used for generation
    embedding_client: Any      # LLM provider used for embeddings
    vectordb_client: Any
    template_parser: Any
    checkpointer: Any          # MongoCheckpointer
    agent_graph: Any           # compiled LangGraph
    notification_engine: Optional[Any] = None
    scheduler: Optional[Any] = None


def services_of(request: Request) -> Services:
    """The running services, or 503 while the app is still starting or has failed to start."""
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The service is not ready")
    return services


def get_services(request: Request) -> Services:
    """FastAPI dependency form of `services_of` (override it in tests)."""
    return services_of(request)
