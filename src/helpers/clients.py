"""The model and vector-store clients, built the same way for the API and for the job worker."""
from contextlib import AsyncExitStack
import logging

from helpers.config import Settings
from stores.llm.LLMProviderFactory import LLMProviderFactory
from stores.llm.templates.template_parser import TemplateParser
from stores.vectordb.VectorDBProviderFactory import VectorDBProviderFactory

logger = logging.getLogger("uvicorn.error")


async def _close_quietly(name: str, close):
    """Shutdown must reach every resource: one that fails to close is logged, not fatal."""
    try:
        await close()
    except Exception:
        logger.warning(f"Failed to close {name}", exc_info=True)


def open_clients(settings: Settings, stack: AsyncExitStack):
    """Create the generation and embedding clients, connect the vector database and load the templates.
    Each is released when `stack` closes. Returns (generation, embedding, vectordb, template_parser)."""
    llm_factory = LLMProviderFactory(settings)
    generation_client = llm_factory.create(provider=settings.GENERATION_BACKEND)
    generation_client.set_generation_model(model_id=settings.GENERATION_MODEL_ID)
    stack.push_async_callback(lambda: _close_quietly("the generation client", generation_client.aclose))

    embedding_client = llm_factory.create(provider=settings.EMBEDDING_BACKEND)
    embedding_client.set_embedding_model(
        model_id=settings.EMBEDDING_MODEL_ID, embedding_size=settings.EMBEDDING_MODEL_SIZE)
    stack.push_async_callback(lambda: _close_quietly("the embedding client", embedding_client.aclose))

    vectordb_client = VectorDBProviderFactory(settings).create(provider=settings.VECTOR_DB_BACKEND)
    vectordb_client.connect()
    stack.callback(vectordb_client.disconnect)

    template_parser = TemplateParser(language=settings.PRIMARY_LANG, default_language=settings.DEFAULT_LANG)
    return generation_client, embedding_client, vectordb_client, template_parser
