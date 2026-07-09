from enum import Enum


class LLMEnums(Enum):
    OPENAI = "OPENAI"
    COHERE  = "COHERE"
    GROQ = "GROQ"
    GEMINI = "GEMINI"


class OpenAIEnums(Enum):
        SYSTEM = "system" # system messages are used to set the behavior of the assistant, and are not visible to the user.
        USER = "user" # user messages are the messages that the user sends to the assistant, and are visible to the user.
        ASSISTANT = "assistant" # assistant messages are the messages that the assistant sends to the user, and are visible to the user.

class CoHereEnums(Enum):
    SYSTEM = "SYSTEM"
    USER = "USER"
    ASSISTANT = "CHATBOT"

    DOCUMENT = "search_document"
    QUERY = "search_query"


class DocumentTypeEnum(Enum):
    DOCUMENT = "document"
    QUERY = "query"


class GroqEnums(Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"