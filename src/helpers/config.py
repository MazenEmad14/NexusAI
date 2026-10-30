from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str
    APP_VERSION: str
    FILE_ALLOWED_TYPES: list
    FILE_MAX_SIZE: int
    FILE_DEFAULT_CHUNK_SIZE: int

    MONGODB_URL: str = "mongodb://localhost:27017/"
    MONGODB_DATABASE: str = "depi"
    POSTGRES_USERNAME: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_MAIN_DATABASE: str

    GENERATION_BACKEND: str
    EMBEDDING_BACKEND: str

    OPENAI_API_KEY: str = None
    OPENAI_API_URL: str = None
    COHERE_API_KEY: str = None
    GROQ_API_KEY: str = None
    GEMINI_API_KEY: str = None

    GENERATION_MODEL_ID: str = None
    EMBEDDING_MODEL_ID: str = None
    EMBEDDING_MODEL_SIZE: int = None
    INPUT_DAFAULT_MAX_CHARACTERS: int = None
    GENERATION_DAFAULT_MAX_TOKENS: int = None
    GENERATION_DAFAULT_TEMPERATURE: float = None
    # Max size of a generation prompt (instructions + data). The INPUT_* limit above only clips embedded text.
    GENERATION_MAX_INPUT_CHARACTERS: int = 24000

    VECTOR_DB_BACKEND : str
    VECTOR_DB_PATH : str
    VECTOR_DB_DISTANCE_METHOD: str = None

    JWT_SECRET_KEY: str
    # Zone used for users who have not set their own (IANA name)
    DEFAULT_TIMEZONE: str = "Africa/Cairo"
    # Sign-in / sign-up throttling (counted in MongoDB, so it holds across workers)
    LOGIN_WINDOW_MINUTES: int = 15
    LOGIN_MAX_FAILURES_PER_ACCOUNT: int = 5   # per email + client address
    LOGIN_MAX_FAILURES_PER_IP: int = 20
    SIGNUP_MAX_PER_IP_PER_HOUR: int = 10
    TOKEN_ENCRYPTION_KEY: Optional[str] = None
    GOOGLE_REDIRECT_URI: str = "http://localhost:5000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:5173"
    # Browser origins allowed to call the API (also the origins the Google login may return to)
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]

    PRIMARY_LANG: str = "en"
    DEFAULT_LANG: str = "en"


    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, value):
        if len(value) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("FILE_ALLOWED_TYPES", mode="before")
    @classmethod
    def parse_file_allowed_types(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

@lru_cache
def get_settings():
    return Settings()
