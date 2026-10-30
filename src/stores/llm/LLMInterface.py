import asyncio
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("uvicorn.error")

# Prompts carry instructions plus data, so the generation limit is generous. The much
# smaller default_input_max_characters only applies to text that is embedded.
DEFAULT_MAX_PROMPT_CHARACTERS = 24000


class LLMInterface(ABC):
    """Common interface for LLM providers.

    Providers implement the sync methods. The async methods (agenerate_text,
    aembed_text, aembed_texts) are what async code (FastAPI routes, LangGraph nodes)
    must call: providers with a native async client override them, and the defaults
    below run the sync version in a worker thread so the event loop is never blocked.
    """

    max_prompt_characters = DEFAULT_MAX_PROMPT_CHARACTERS

    @abstractmethod
    def set_generation_model(self, model_id: str):
        pass

    @abstractmethod
    def set_embedding_model(self, model_id: str, embedding_size: int):
        pass

    @abstractmethod
    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None):
        pass

    @abstractmethod
    def embed_text(self, text: str, document_type: str = None):
        pass

    @abstractmethod
    def embed_texts(self, texts: list[str], document_type: str = None):
        pass

    @abstractmethod
    def construct_prompt(self, prompt: str, role: str):
        pass

    # ── async API ────────────────────────────────────────────────────────────
    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None):
        return await asyncio.to_thread(self.generate_text, prompt, chat_history, max_output_tokens, temperature)

    async def aembed_text(self, text: str, document_type: str = None):
        return await asyncio.to_thread(self.embed_text, text, document_type)

    async def aembed_texts(self, texts: list[str], document_type: str = None):
        return await asyncio.to_thread(self.embed_texts, texts, document_type)

    async def aclose(self):
        """Release network resources held by async clients (no-op by default)."""

    # ── shared helpers ───────────────────────────────────────────────────────
    @staticmethod
    def _limit(text: str, limit: int, what: str) -> str:
        text = text or ""
        if limit and len(text) > limit:
            logger.warning(f"Truncating {what} from {len(text)} to {limit} characters")
            return text[:limit]
        return text

    def process_text(self, text: str) -> str:
        """Clip text that will be embedded to the embedding input limit."""
        return self._limit(text, getattr(self, "default_input_max_characters", 0), "embedding input").strip()

    def process_prompt(self, text: str) -> str:
        """Clip a generation prompt or message to the (large) prompt limit, not the embedding limit."""
        return self._limit(text, self.max_prompt_characters, "prompt").strip()

    def _generation_params(self, max_output_tokens, temperature):
        """Apply defaults only when a value is missing; 0 and 0.0 are legitimate values."""
        return (
            max_output_tokens if max_output_tokens is not None else self.default_generation_max_output_tokens,
            temperature if temperature is not None else self.default_generation_temperature,
        )
