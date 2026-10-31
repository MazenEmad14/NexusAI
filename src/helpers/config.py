"""Every setting the application reads, in one place.

Nothing else reads the environment: code asks `get_settings()`. Two layers of checking:

* constructing `Settings` rejects values that are malformed on their own (a negative size, an unknown
  backend name, a bad time zone), so importing the app with such a value fails at once;
* `Settings.problems()` checks the settings against each other (the chosen backend has its API key and
  model, the language has templates ...). The app calls it at startup and refuses to start with a
  readable list, instead of failing on the first request that needs the missing piece.
"""
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SRC_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = SRC_DIR / ".env"

# LLM backends the factory can build, and what each can do.
GENERATION_BACKENDS = ("OPENAI", "COHERE", "GROQ", "GEMINI")
EMBEDDING_BACKENDS = ("OPENAI", "COHERE", "GEMINI")  # Groq has no embeddings API
BACKEND_API_KEY = {"OPENAI": "OPENAI_API_KEY", "COHERE": "COHERE_API_KEY", "GROQ": "GROQ_API_KEY", "GEMINI": "GEMINI_API_KEY"}
VECTOR_DB_BACKENDS = ("QDRANT",)
DISTANCE_METHODS = ("cosine", "dot")


class ConfigurationError(RuntimeError):
    """The settings are inconsistent; the message lists every problem."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    APP_NAME: str
    APP_VERSION: str

    # ── uploads ──────────────────────────────────────────────────────────────
    FILE_ALLOWED_TYPES: list
    FILE_MAX_SIZE: int = Field(gt=0)               # megabytes per file
    FILE_DEFAULT_CHUNK_SIZE: int = Field(gt=0)     # bytes read per step while streaming an upload

    # ── databases ────────────────────────────────────────────────────────────
    MONGODB_URL: str = "mongodb://localhost:27017/"
    MONGODB_DATABASE: str = "depi"
    POSTGRES_USERNAME: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int = Field(gt=0, lt=65536)
    POSTGRES_MAIN_DATABASE: str
    VECTOR_DB_BACKEND: str
    VECTOR_DB_PATH: str
    VECTOR_DB_DISTANCE_METHOD: str = "cosine"

    # ── LLM providers ────────────────────────────────────────────────────────
    GENERATION_BACKEND: str
    EMBEDDING_BACKEND: str
    GENERATION_MODEL_ID: Optional[str] = None
    EMBEDDING_MODEL_ID: Optional[str] = None
    EMBEDDING_MODEL_SIZE: Optional[int] = Field(default=None, gt=0)
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_API_URL: Optional[str] = None
    COHERE_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    # The older spellings (INPUT_DAFAULT_..., GENERATION_DAFAULT_...) are still accepted from the environment.
    INPUT_DEFAULT_MAX_CHARACTERS: int = Field(
        default=1024, gt=0, validation_alias=AliasChoices("INPUT_DEFAULT_MAX_CHARACTERS", "INPUT_DAFAULT_MAX_CHARACTERS"))
    GENERATION_DEFAULT_MAX_TOKENS: int = Field(
        default=512, gt=0, validation_alias=AliasChoices("GENERATION_DEFAULT_MAX_TOKENS", "GENERATION_DAFAULT_MAX_TOKENS"))
    GENERATION_DEFAULT_TEMPERATURE: float = Field(
        default=0.1, ge=0, le=2, validation_alias=AliasChoices("GENERATION_DEFAULT_TEMPERATURE", "GENERATION_DAFAULT_TEMPERATURE"))
    # Largest generation prompt (instructions + data); INPUT_DEFAULT_MAX_CHARACTERS only clips embedded text.
    GENERATION_MAX_INPUT_CHARACTERS: int = Field(default=24000, gt=0)

    # ── security ─────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    TOKEN_ENCRYPTION_KEY: Optional[str] = None     # encrypts stored Google tokens when set

    # ── web, Google and CORS ─────────────────────────────────────────────────
    GOOGLE_REDIRECT_URI: str = "http://localhost:5000/api/v1/auth/google/callback"
    FRONTEND_URL: str = "http://localhost:5173"
    # Browser origins allowed to call the API (also the origins the Google login may return to)
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]
    # Optional: Google Custom Search for the web-search step (DuckDuckGo is used without them)
    GOOGLE_SEARCH_API_KEY: Optional[str] = None
    GOOGLE_SEARCH_CX: Optional[str] = None

    # ── behaviour ────────────────────────────────────────────────────────────
    PRIMARY_LANG: str = "en"
    DEFAULT_LANG: str = "en"
    DEFAULT_TIMEZONE: str = "Africa/Cairo"         # zone for users who have not set their own (IANA name)
    DAILY_SUMMARY_HOUR: int = Field(default=8, ge=0, le=23)           # the briefing goes out at this local hour
    INBOX_CHECK_INTERVAL_MINUTES: int = Field(default=1, ge=1)        # proactive check for urgent email
    THREAD_RETENTION_DAYS: int = Field(default=30, ge=1)              # idle conversations are deleted after this
    WHISPER_MODEL: str = "base"                    # speech-to-text model size (only with requirements-voice.txt)

    # Sign-in / sign-up throttling (counted in MongoDB, so it holds across workers)
    LOGIN_WINDOW_MINUTES: int = Field(default=15, ge=1)
    LOGIN_MAX_FAILURES_PER_ACCOUNT: int = Field(default=5, ge=1)   # per email + client address
    LOGIN_MAX_FAILURES_PER_IP: int = Field(default=20, ge=1)
    SIGNUP_MAX_PER_IP_PER_HOUR: int = Field(default=10, ge=1)

    # ── field-level checks ───────────────────────────────────────────────────
    @field_validator(
        "GENERATION_MODEL_ID", "EMBEDDING_MODEL_ID", "EMBEDDING_MODEL_SIZE", "OPENAI_API_KEY", "OPENAI_API_URL",
        "COHERE_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "TOKEN_ENCRYPTION_KEY", "GOOGLE_SEARCH_API_KEY",
        "GOOGLE_SEARCH_CX", mode="before",
    )
    @classmethod
    def blank_means_unset(cls, value):
        """`KEY=` in an env file is an empty string; treat it as not set."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, value):
        if len(value) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("FILE_ALLOWED_TYPES", mode="before")
    @classmethod
    def parse_file_allowed_types(cls, value):
        if isinstance(value, str) and not value.lstrip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("GENERATION_BACKEND", "EMBEDDING_BACKEND", "VECTOR_DB_BACKEND", mode="before")
    @classmethod
    def uppercase_backend(cls, value):
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("GENERATION_BACKEND")
    @classmethod
    def known_generation_backend(cls, value):
        if value not in GENERATION_BACKENDS:
            raise ValueError(f"must be one of {', '.join(GENERATION_BACKENDS)}")
        return value

    @field_validator("EMBEDDING_BACKEND")
    @classmethod
    def known_embedding_backend(cls, value):
        if value not in EMBEDDING_BACKENDS:
            raise ValueError(f"must be one of {', '.join(EMBEDDING_BACKENDS)} (Groq cannot create embeddings)")
        return value

    @field_validator("VECTOR_DB_BACKEND")
    @classmethod
    def known_vector_db(cls, value):
        if value not in VECTOR_DB_BACKENDS:
            raise ValueError(f"must be one of {', '.join(VECTOR_DB_BACKENDS)}")
        return value

    @field_validator("VECTOR_DB_DISTANCE_METHOD", mode="before")
    @classmethod
    def known_distance(cls, value):
        value = value.strip().lower() if isinstance(value, str) else value
        if value not in DISTANCE_METHODS:
            raise ValueError(f"must be one of {', '.join(DISTANCE_METHODS)}")
        return value

    @field_validator("MONGODB_URL")
    @classmethod
    def mongodb_url(cls, value):
        if not value.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("must start with mongodb:// or mongodb+srv://")
        return value

    @field_validator("FRONTEND_URL", "GOOGLE_REDIRECT_URI")
    @classmethod
    def http_url(cls, value):
        if not value.startswith(("http://", "https://")):
            raise ValueError("must start with http:// or https://")
        return value.rstrip("/") if value.count("/") <= 2 else value

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, value):
        if isinstance(value, str) and not value.lstrip().startswith("["):
            value = [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("CORS_ORIGINS")
    @classmethod
    def valid_origins(cls, value):
        for origin in value:
            if not origin.startswith(("http://", "https://")) or origin.endswith("/") or "*" in origin:
                raise ValueError(f"{origin!r} must look like https://host[:port] (no path, no trailing slash, no wildcard)")
        return value

    @field_validator("DEFAULT_TIMEZONE")
    @classmethod
    def known_timezone(cls, value):
        from zoneinfo import ZoneInfo

        try:
            ZoneInfo(value)
        except Exception:
            raise ValueError("must be an IANA time zone name such as Africa/Cairo")
        return value

    # ── checks across settings (run at startup) ──────────────────────────────
    def problems(self) -> list[str]:
        """Everything that would stop the app working properly, as sentences for the operator."""
        found = []

        for role, backend in (("generation", self.GENERATION_BACKEND), ("embedding", self.EMBEDDING_BACKEND)):
            key_name = BACKEND_API_KEY[backend]
            if not getattr(self, key_name):
                found.append(f"{role} uses {backend}, so {key_name} must be set")
        if not self.GENERATION_MODEL_ID:
            found.append("GENERATION_MODEL_ID must be set")
        if not self.EMBEDDING_MODEL_ID:
            found.append("EMBEDDING_MODEL_ID must be set")
        if not self.EMBEDDING_MODEL_SIZE:
            found.append("EMBEDDING_MODEL_SIZE must be set (the vector size of the embedding model)")

        from helpers.file_types import allowed_extensions

        if not allowed_extensions(self.FILE_ALLOWED_TYPES):
            found.append("FILE_ALLOWED_TYPES does not contain any supported type (text/plain, application/pdf, text/csv, ...)")

        languages = available_languages()
        for name in ("PRIMARY_LANG", "DEFAULT_LANG"):
            if getattr(self, name) not in languages:
                found.append(f"{name}={getattr(self, name)!r} has no prompt templates (available: {', '.join(sorted(languages))})")

        if self.TOKEN_ENCRYPTION_KEY:
            try:
                from cryptography.fernet import Fernet

                Fernet(self.TOKEN_ENCRYPTION_KEY.encode())
            except Exception:
                found.append("TOKEN_ENCRYPTION_KEY is not a valid Fernet key (generate one with Fernet.generate_key())")

        if bool(self.GOOGLE_SEARCH_API_KEY) != bool(self.GOOGLE_SEARCH_CX):
            found.append("GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX must be set together")
        if self.LOGIN_MAX_FAILURES_PER_IP < self.LOGIN_MAX_FAILURES_PER_ACCOUNT:
            found.append("LOGIN_MAX_FAILURES_PER_IP should not be lower than LOGIN_MAX_FAILURES_PER_ACCOUNT")
        return found

    def ensure_valid(self) -> None:
        """Raise ConfigurationError listing every problem (called once at startup)."""
        found = self.problems()
        if found:
            raise ConfigurationError("Invalid configuration:\n" + "\n".join(f"  - {line}" for line in found))


def available_languages() -> set:
    """Languages that have prompt templates (one folder each under stores/llm/templates/locales)."""
    locales = SRC_DIR / "stores" / "llm" / "templates" / "locales"
    return {p.name for p in locales.iterdir() if p.is_dir() and not p.name.startswith("__")}


def setting_names() -> set:
    """Every environment variable name the settings accept, including the older spellings."""
    names = set()
    for name, field in Settings.model_fields.items():
        names.add(name)
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            names.update(a for a in alias.choices if isinstance(a, str))
    return {n.upper() for n in names}


def unknown_env_keys(env_file: Path = ENV_FILE) -> list[str]:
    """Names in the env file that match no setting: usually a typo that would otherwise be ignored silently."""
    if not Path(env_file).exists():
        return []
    from dotenv import dotenv_values

    known = setting_names()
    return sorted(key for key in dotenv_values(env_file) if key.upper() not in known)


@lru_cache
def get_settings():
    return Settings()
