from openai import OpenAI
import httpx
import logging
from ..LLMInterface import LLMInterface
from ..LLMEnums import OpenAIEnums

class OpenAIProvider(LLMInterface):


    def __init__(self , api_key :str ,
                 api_url :str =None,
                 default_input_max_characters : int =1000,
                 default_generation_max_output_tokens : int = 1000,
                 default_generation_temperature :float =0.1
                 ):
        
        self.api_key=api_key
        self.api_url =api_url

        self.default_input_max_characters=default_input_max_characters
        self.default_generation_max_output_tokens=default_generation_max_output_tokens
        self.default_generation_temperature=default_generation_temperature
        

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

        self.logger = logging.getLogger(__name__)
    
    
    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id
    
    def set_embedding_model(self, model_id: str, embedding_size: int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size
    
    def process_text(self, text: str):
        return text[:self.default_input_max_characters].strip()
    
    def generate_text(self, prompt: str, chat_history: list=None, max_output_tokens: int=None,
                            temperature: float = None):
        

        if not self.client :
            self.logger.error("OpenAI client was not set")
            return None
        
        if not self.generation_model_id :
            self.logger.error("Generation model for OpenAI was not set")
            return None
        
        max_output_tokens = max_output_tokens if max_output_tokens else self.default_generation_max_output_tokens
        temperature = temperature if temperature is not None else self.default_generation_temperature

        chat_history_copy = chat_history.copy() if chat_history is not None else []
        chat_history_copy.append(self.construct_prompt(prompt =prompt ,role=OpenAIEnums.USER.value))

        response = self.client.chat.completions.create(
            model=self.generation_model_id,
            messages=chat_history_copy,
            temperature=temperature,
            max_tokens =max_output_tokens
        )

        if not response or not response.choices or len(response.choices)==0 or not response.choices[0].message:
                        self.logger.error("Error while generating text with OpenAI")
                        return None

        return response.choices[0].message.content


    def embed_text(self, text: str, document_type: str = None):
        embeddings = self.embed_texts([text], document_type=document_type)
        if not embeddings:
            return None
        return embeddings[0]
    
    def embed_texts(self, texts: list[str], document_type: str = None):
        
        
        if not self.client :
            self.logger.error("OpenAI client was not set")
            return None
        
        if not self.embedding_model_id :
            self.logger.error("Generation model for OpenAI was not set")
            return None
        
        processed_texts = [
            self.process_text(text or "") or " "
            for text in texts
        ]

        if not processed_texts:
            return []
        
        response = self.client.embeddings.create(
	             model=self.embedding_model_id,
	            input=processed_texts
        )
        if not response or not response.data or len(response.data)==0:
	                        self.logger.error("Error while generating text with OpenAI")
	                        return None

        return [
            item.embedding
            for item in sorted(response.data, key=lambda item: item.index)
        ]
        
    

    def construct_prompt(self, prompt: str, role: str):
        return {
            "role" :role,
            "content" : self.process_text(prompt)
        }

    def generate_audio_from_text(self, text: str) -> bytes:
        if not self.client:
            self.logger.error("OpenAI client was not set for TTS")
            return b""
        
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
