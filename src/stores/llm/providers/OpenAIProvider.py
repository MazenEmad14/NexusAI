import logging

import httpx
from openai import AsyncOpenAI, OpenAI

from ..LLMEnums import OpenAIEnums
from ..LLMInterface import LLMInterface


class OpenAIProvider(LLMInterface):

    def __init__(self, api_key: str,
                 api_url: str = None,
                 default_input_max_characters: int = 1000,
                 default_generation_max_output_tokens: int = 1000,
                 default_generation_temperature: float = 0.1):

        self.api_key = api_key
        self.api_url = api_url

        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temperature = default_generation_temperature

        self.generation_model_id = None

        self.embedding_model_id = None
        self.embedding_size = None
        self.enum = OpenAIEnums
        self.enums = OpenAIEnums

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.api_url,
            http_client=httpx.Client(timeout=30.0)
        )
        self.async_client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_url,
            http_client=httpx.AsyncClient(timeout=30.0)
        )

        self.logger = logging.getLogger(__name__)

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id

    def set_embedding_model(self, model_id: str, embedding_size: int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size

    def _chat_kwargs(self, prompt, chat_history, max_output_tokens, temperature):
        max_output_tokens, temperature = self._generation_params(max_output_tokens, temperature)
        messages = list(chat_history) if chat_history else []
        messages.append(self.construct_prompt(prompt=prompt, role=OpenAIEnums.USER.value))
        return {
            "model": self.generation_model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

    def _extract_text(self, response):
        if not response or not response.choices or not response.choices[0].message:
            self.logger.error("Error while generating text with OpenAI")
            return None
        return response.choices[0].message.content

    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None):
        if not self.generation_model_id:
            self.logger.error("Generation model for OpenAI was not set")
            return None
        response = self.client.chat.completions.create(
            **self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature)
        )
        return self._extract_text(response)

    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None):
        if not self.generation_model_id:
            self.logger.error("Generation model for OpenAI was not set")
            return None
        response = await self.async_client.chat.completions.create(
            **self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature)
        )
        return self._extract_text(response)

    def _processed_texts(self, texts: list[str]) -> list[str]:
        return [self.process_text(text or "") or " " for text in texts]

    def _extract_embeddings(self, response):
        if not response or not response.data:
            self.logger.error("Error while embedding text with OpenAI")
            return None
        return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]

    def embed_text(self, text: str, document_type: str = None):
        embeddings = self.embed_texts([text], document_type=document_type)
        return embeddings[0] if embeddings else None

    def embed_texts(self, texts: list[str], document_type: str = None):
        if not self.embedding_model_id:
            self.logger.error("Embedding model for OpenAI was not set")
            return None
        processed = self._processed_texts(texts)
        if not processed:
            return []
        response = self.client.embeddings.create(model=self.embedding_model_id, input=processed)
        return self._extract_embeddings(response)

    async def aembed_text(self, text: str, document_type: str = None):
        embeddings = await self.aembed_texts([text], document_type=document_type)
        return embeddings[0] if embeddings else None

    async def aembed_texts(self, texts: list[str], document_type: str = None):
        if not self.embedding_model_id:
            self.logger.error("Embedding model for OpenAI was not set")
            return None
        processed = self._processed_texts(texts)
        if not processed:
            return []
        response = await self.async_client.embeddings.create(model=self.embedding_model_id, input=processed)
        return self._extract_embeddings(response)

    async def aclose(self):
        await self.async_client.close()

    def construct_prompt(self, prompt: str, role: str):
        return {
            "role": role,
            "content": self.process_prompt(prompt)
        }

    def generate_audio_from_text(self, text: str) -> bytes:
        try:
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text
            )
            return response.content
        except Exception as e:
            self.logger.error(f"Error generating audio from text: {e}")
            return b""
