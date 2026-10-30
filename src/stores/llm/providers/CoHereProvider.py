from ..LLMInterface import LLMInterface
from ..LLMEnums import CoHereEnums, DocumentTypeEnum
import cohere
import logging

class CoHereProvider(LLMInterface):

    def __init__(self, api_key: str,
                       default_input_max_characters: int=1000,
                       default_generation_max_output_tokens: int=1000,
                       default_generation_temperature: float=0.1):
        
        self.api_key = api_key

        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temperature = default_generation_temperature

        self.generation_model_id = None

        self.embedding_model_id = None
        self.embedding_size = None
        self.enum = CoHereEnums
        self.enums = CoHereEnums
        self.client = cohere.Client(api_key=self.api_key, timeout=30.0)
        self.async_client = cohere.AsyncClient(api_key=self.api_key, timeout=30.0)

        self.logger = logging.getLogger(__name__)

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id

    def set_embedding_model(self, model_id: str, embedding_size: int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size

    def _chat_kwargs(self, prompt, chat_history, max_output_tokens, temperature):
        max_output_tokens, temperature = self._generation_params(max_output_tokens, temperature)
        return {
            "model": self.generation_model_id,
            "chat_history": chat_history or [],
            "message": self.process_prompt(prompt),
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

    def _extract_text(self, response):
        if not response or not response.text:
            self.logger.error("Error while generating text with CoHere")
            return None
        return response.text

    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None):
        if not self.generation_model_id:
            self.logger.error("Generation model for CoHere was not set")
            return None
        response = self.client.chat(**self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature))
        return self._extract_text(response)

    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None):
        if not self.generation_model_id:
            self.logger.error("Generation model for CoHere was not set")
            return None
        response = await self.async_client.chat(**self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature))
        return self._extract_text(response)

    def embed_text(self, text: str, document_type: str = None):
        embeddings = self.embed_texts([text], document_type=document_type)
        if not embeddings:
            return None
        return embeddings[0]

    _EMBED_BATCH_SIZE = 96

    def _embed_kwargs(self, document_type):
        input_type = CoHereEnums.DOCUMENT.value
        if document_type in (DocumentTypeEnum.QUERY, DocumentTypeEnum.QUERY.value):
            input_type = CoHereEnums.QUERY.value
        return {"model": self.embedding_model_id, "input_type": input_type, "embedding_types": ["float"]}

    def _batches(self, texts: list[str]):
        processed = [self.process_text(text or "") or " " for text in texts]
        return [processed[i:i + self._EMBED_BATCH_SIZE] for i in range(0, len(processed), self._EMBED_BATCH_SIZE)]

    def embed_texts(self, texts: list[str], document_type: str = None):
        if not self.embedding_model_id:
            self.logger.error("Embedding model for CoHere was not set")
            return None
        kwargs = self._embed_kwargs(document_type)
        embeddings = []
        for batch in self._batches(texts):
            response = self.client.embed(texts=batch, **kwargs)
            if not response or not response.embeddings or not response.embeddings.float:
                self.logger.error("Error while embedding text with CoHere")
                return None
            embeddings.extend(response.embeddings.float)
        return embeddings

    async def aembed_text(self, text: str, document_type: str = None):
        embeddings = await self.aembed_texts([text], document_type=document_type)
        return embeddings[0] if embeddings else None

    async def aembed_texts(self, texts: list[str], document_type: str = None):
        if not self.embedding_model_id:
            self.logger.error("Embedding model for CoHere was not set")
            return None
        kwargs = self._embed_kwargs(document_type)
        embeddings = []
        for batch in self._batches(texts):
            response = await self.async_client.embed(texts=batch, **kwargs)
            if not response or not response.embeddings or not response.embeddings.float:
                self.logger.error("Error while embedding text with CoHere")
                return None
            embeddings.extend(response.embeddings.float)
        return embeddings

    def construct_prompt(self, prompt: str, role: str):
        return {
            "role": role,
            "text": self.process_prompt(prompt)
        }
