import logging
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from ..LLMInterface import LLMInterface
from ..LLMEnums import LLMEnums, OpenAIEnums

class GeminiProvider(LLMInterface):
    def __init__(self, api_key: str, 
                 default_input_max_characters: int = 4000,
                 default_generation_max_output_tokens: int = 1000,
                 default_generation_temperature: float = 0.1):
        
        self.api_key = api_key
        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temperature = default_generation_temperature
        
        self.generation_model_id = "gemini-2.5-flash"
        self.embedding_model_id = "models/embedding-001"
        self.embedding_size = 768
        self.enum = LLMEnums.GEMINI
        self.enums = OpenAIEnums
        
        self.logger = logging.getLogger(__name__)

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id

    def set_embedding_model(self, model_id: str, embedding_size: int = 768):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size

    def _llm(self, max_output_tokens, temperature):
        max_output_tokens, temperature = self._generation_params(max_output_tokens, temperature)
        return ChatGoogleGenerativeAI(
            model=self.generation_model_id,
            google_api_key=self.api_key,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )

    def _messages(self, prompt, chat_history):
        messages = list(chat_history) if chat_history else []
        messages.append({"role": "user", "content": self.process_prompt(prompt)})
        return messages

    def generate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                      temperature: float = None, json_mode: bool = False) -> str:
        if not self.api_key:
            self.logger.error("Gemini API key is not set")
            return None
        try:
            return self._llm(max_output_tokens, temperature).invoke(self._messages(prompt, chat_history)).content
        except Exception as e:
            self.logger.error(f"Error generating text with Gemini: {e}")
            return None

    async def agenerate_text(self, prompt: str, chat_history: list = None, max_output_tokens: int = None,
                             temperature: float = None, json_mode: bool = False) -> str:
        if not self.api_key:
            self.logger.error("Gemini API key is not set")
            return None
        try:
            response = await self._llm(max_output_tokens, temperature).ainvoke(self._messages(prompt, chat_history))
            return response.content
        except Exception as e:
            self.logger.error(f"Error generating text with Gemini: {e}")
            return None

    def embed_text(self, text: str, document_type: str = None) -> list:
        if not self.api_key:
            self.logger.error("Gemini API key is not set for embeddings")
            return None
            
        try:
            embeddings = GoogleGenerativeAIEmbeddings(
                model=self.embedding_model_id,
                google_api_key=self.api_key
            )
            processed_text = self.process_text(text)
            if not processed_text:
                return []
            return embeddings.embed_query(processed_text)
        except Exception as e:
            self.logger.error(f"Error embedding text with Gemini: {e}")
            return None

    def embed_chunk(self, chunk: dict) -> list:
        return self.embed_text(chunk.get("text", ""))

    def embed_texts(self, texts: list[str], document_type: str = None) -> list[list[float]]:
        if not self.api_key:
            self.logger.error("Gemini API key is not set for embeddings")
            return None
            
        try:
            embeddings = GoogleGenerativeAIEmbeddings(
                model=self.embedding_model_id,
                google_api_key=self.api_key
            )
            processed_texts = [self.process_text(t) for t in texts]
            return embeddings.embed_documents(processed_texts)
        except Exception as e:
            self.logger.error(f"Error embedding texts with Gemini: {e}")
            return None

    def construct_prompt(self, prompt: str, role: str):
        return {
            "role": role,
            "content": self.process_prompt(prompt)
        }

    def generate_audio_from_text(self, text: str) -> bytes:
        pass
