from ..LLMInterface import LLMInterface
from ..LLMEnums import GroqEnums
from groq import Groq
import logging

class GroqProvider(LLMInterface):
    def __init__(self,api_key:str,
                      default_input_max_characters:int=1000,
                      default_generation_max_output_tokens: int=1000,
                      default_generation_temperature: float=0.1):
        
        
        self.api_key = api_key    
        self.default_input_max_characters = default_input_max_characters
        self.default_generation_max_output_tokens = default_generation_max_output_tokens
        self.default_generation_temperature = default_generation_temperature     
        self.generation_model_id = None
        self.enums=GroqEnums
        self.client = Groq(api_key=self.api_key)
        self.logger = logging.getLogger(__name__)

    def set_generation_model(self, model_id: str):
        self.generation_model_id = model_id
    
    def set_embedding_model(self, model_id: str, embedding_size: int):
        self.embedding_model_id = model_id
        self.embedding_size = embedding_size
    
    def process_text(self, text: str):
        return text[:self.default_input_max_characters].strip()
    def generate_text(self,prompt:str,chat_history:list=[] ,max_output_tokens:int = None,
                            temperature:float=None):
        if not self.client:
            self.logger.error("client for open ai was not set")
            return None
        if not self.generation_model_id:
            self.logger.error("generation model for open ai was not set")
            return None

        max_output_tokens = max_output_tokens if max_output_tokens else self.default_generation_max_output_tokens
        temperature = temperature if temperature else self.default_generation_temperature

        chat_history_copy = chat_history.copy() if chat_history else []
        chat_history_copy.append(self.construct_prompt(prompt=prompt,role=GroqEnums.USER.value))
        response = self.client.chat.completions.create(
            model = self.generation_model_id,
            messages = chat_history_copy,
          )
        
        if not response or not response.choices[0].message.content:
            self.logger.error("Error while generating text with Cohere")

        return response.choices[0].message.content
    
   
    def embed_text(self,text:str,document_type:str=None):
        pass

    def embed_texts(self, texts: list[str], document_type: str = None):
        return [
            self.embed_text(text=text, document_type=document_type)
            for text in texts
        ]

    def construct_prompt(self,prompt:str,role:str):
        return{
            "role" : role,
            "content" : self.process_text(prompt)
        }
