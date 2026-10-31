import logging

from groq import AsyncGroq, Groq

from ..LLMEnums import GroqEnums
from ..LLMInterface import LLMInterface


class GroqProvider(LLMInterface):
    def __init__(self, api_key: str,
                 default_input_max_characters: int = 1000,
                 default_generation_max_output_tokens: int = 1000,
                 default_generation_temperature: float = 0.1):

        self.api_key = api_key
        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temperature = default_generation_temperature
        self.generation_model_id = None
        self.embedding_model_id = None
        self.embedding_size = None
        self.enums = GroqEnums
        self.client = Groq(api_key=self.api_key, timeout=30.0)
        self.async_client = AsyncGroq(api_key=self.api_key, timeout=30.0)
        self.logger = logging.getLogger(__name__)

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id

    def set_embedding_model(self, model_id: str, embedding_size: int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size

    def _chat_kwargs(self, prompt, chat_history, max_output_tokens, temperature, json_mode=False):
        max_output_tokens, temperature = self._generation_params(max_output_tokens, temperature)
        messages = list(chat_history) if chat_history else []
        messages.append(self.construct_prompt(prompt=prompt, role=GroqEnums.USER.value))
        kwargs = {
            "model": self.generation_model_id,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def _extract_text(self, response):
        if not response or not response.choices or not response.choices[0].message or not response.choices[0].message.content:
            self.logger.error("Error while generating text with Groq")
            return None
        return response.choices[0].message.content

    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None, json_mode: bool = False):
        if not self.generation_model_id:
            self.logger.error("Generation model for Groq was not set")
            return None
        response = self.client.chat.completions.create(
            **self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature, json_mode)
        )
        return self._extract_text(response)

    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None, json_mode: bool = False):
        if not self.generation_model_id:
            self.logger.error("Generation model for Groq was not set")
            return None
        response = await self.async_client.chat.completions.create(
            **self._chat_kwargs(prompt, chat_history, max_output_tokens, temperature, json_mode)
        )
        return self._extract_text(response)

    async def aclose(self):
        await self.async_client.close()

    def embed_text(self, text: str, document_type: str = None):
        self.logger.error("Groq does not provide embeddings")
        return None

    def embed_texts(self, texts: list[str], document_type: str = None):
        self.logger.error("Groq does not provide embeddings")
        return None

    def construct_prompt(self, prompt: str, role: str):
        return {
            "role": role,
            "content": self.process_prompt(prompt)
        }
