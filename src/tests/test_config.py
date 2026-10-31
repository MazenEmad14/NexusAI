"""Configuration: one place reads it, bad values are rejected, and the app will not start half-configured
(plan 3.7)."""
import asyncio
import re
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from helpers.config import (
    ConfigurationError, Settings, available_languages, get_settings, setting_names, unknown_env_keys,
)

SRC = Path(__file__).resolve().parents[1]

# The smallest set of values that makes a valid Settings; tests change one thing at a time.
VALID = dict(
    APP_NAME="app", APP_VERSION="1", FILE_ALLOWED_TYPES=["text/plain"], FILE_MAX_SIZE=10, FILE_DEFAULT_CHUNK_SIZE=1024,
    GENERATION_BACKEND="GROQ", EMBEDDING_BACKEND="COHERE", VECTOR_DB_BACKEND="QDRANT", VECTOR_DB_PATH="qdrant_db",
    JWT_SECRET_KEY="k" * 40, GENERATION_MODEL_ID="gen", EMBEDDING_MODEL_ID="emb", EMBEDDING_MODEL_SIZE=1024,
    GROQ_API_KEY="g", COHERE_API_KEY="c",
)


def make(**changes):
    return Settings(_env_file=None, **{**VALID, **changes})


# ── field-level validation ───────────────────────────────────────────────────
def test_the_minimal_valid_settings_have_no_problems():
    assert make().problems() == []


@pytest.mark.parametrize("change", [
    {"FILE_MAX_SIZE": 0}, {"FILE_DEFAULT_CHUNK_SIZE": -1},
    {"GENERATION_BACKEND": "NOPE"}, {"EMBEDDING_BACKEND": "GROQ"}, {"VECTOR_DB_BACKEND": "PINECONE"},
    {"VECTOR_DB_DISTANCE_METHOD": "manhattan"}, {"MONGODB_URL": "postgres://x"},
    {"DEFAULT_TIMEZONE": "Mars/Olympus"}, {"DAILY_SUMMARY_HOUR": 24}, {"INBOX_CHECK_INTERVAL_MINUTES": 0},
    {"THREAD_RETENTION_DAYS": 0}, {"GENERATION_DEFAULT_TEMPERATURE": 5}, {"INPUT_DEFAULT_MAX_CHARACTERS": 0},
    {"JWT_SECRET_KEY": "short"}, {"FRONTEND_URL": "localhost:5173"}, {"EMBEDDING_MODEL_SIZE": 0},
    {"CORS_ORIGINS": ["http://localhost:5173/"]}, {"CORS_ORIGINS": ["*"]}, {"CORS_ORIGINS": ["http://*.example.com"]},
])
def test_malformed_values_are_rejected_when_the_settings_are_built(change):
    with pytest.raises(ValidationError):
        make(**change)


def test_values_are_normalised():
    settings = make(GENERATION_BACKEND=" groq ", VECTOR_DB_DISTANCE_METHOD="COSINE", OPENAI_API_KEY="  ",
                    OPENAI_API_URL="", TOKEN_ENCRYPTION_KEY="", GENERATION_MODEL_ID="", FILE_ALLOWED_TYPES="text/plain, application/pdf",
                    CORS_ORIGINS="http://a.example, http://b.example")
    assert settings.GENERATION_BACKEND == "GROQ" and settings.VECTOR_DB_DISTANCE_METHOD == "cosine"
    assert settings.OPENAI_API_KEY is None and settings.OPENAI_API_URL is None  # `KEY=` in an env file means unset
    assert settings.TOKEN_ENCRYPTION_KEY is None and settings.GENERATION_MODEL_ID is None
    assert settings.FILE_ALLOWED_TYPES == ["text/plain", "application/pdf"]
    assert settings.CORS_ORIGINS == ["http://a.example", "http://b.example"]


def test_the_older_misspelt_names_are_still_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("INPUT_DAFAULT_MAX_CHARACTERS", "2048")
    monkeypatch.setenv("GENERATION_DAFAULT_MAX_TOKENS", "300")
    monkeypatch.setenv("GENERATION_DAFAULT_TEMPERATURE", "0.5")
    settings = make()
    assert (settings.INPUT_DEFAULT_MAX_CHARACTERS, settings.GENERATION_DEFAULT_MAX_TOKENS, settings.GENERATION_DEFAULT_TEMPERATURE) == (2048, 300, 0.5)

    monkeypatch.delenv("INPUT_DAFAULT_MAX_CHARACTERS")
    monkeypatch.setenv("INPUT_DEFAULT_MAX_CHARACTERS", "4096")
    assert make().INPUT_DEFAULT_MAX_CHARACTERS == 4096  # and the correct spelling works too


def test_settings_have_usable_defaults_instead_of_none():
    settings = make()
    assert settings.INPUT_DEFAULT_MAX_CHARACTERS == 1024 and settings.GENERATION_DEFAULT_MAX_TOKENS == 512
    assert settings.DAILY_SUMMARY_HOUR == 8 and settings.THREAD_RETENTION_DAYS == 30 and settings.WHISPER_MODEL == "base"


# ── checks across settings ───────────────────────────────────────────────────
@pytest.mark.parametrize("change, fragment", [
    ({"GROQ_API_KEY": None}, "GROQ_API_KEY"),
    ({"COHERE_API_KEY": ""}, "COHERE_API_KEY"),
    ({"GENERATION_BACKEND": "OPENAI"}, "OPENAI_API_KEY"),
    ({"GENERATION_MODEL_ID": None}, "GENERATION_MODEL_ID"),
    ({"EMBEDDING_MODEL_ID": None}, "EMBEDDING_MODEL_ID"),
    ({"EMBEDDING_MODEL_SIZE": None}, "EMBEDDING_MODEL_SIZE"),
    ({"FILE_ALLOWED_TYPES": ["image/png"]}, "FILE_ALLOWED_TYPES"),
    ({"PRIMARY_LANG": "fr"}, "PRIMARY_LANG"),
    ({"DEFAULT_LANG": "xx"}, "DEFAULT_LANG"),
    ({"TOKEN_ENCRYPTION_KEY": "not-a-fernet-key"}, "TOKEN_ENCRYPTION_KEY"),
    ({"GOOGLE_SEARCH_API_KEY": "k"}, "GOOGLE_SEARCH"),
    ({"LOGIN_MAX_FAILURES_PER_ACCOUNT": 30}, "LOGIN_MAX_FAILURES_PER_IP"),
])
def test_inconsistent_settings_are_reported(change, fragment):
    found = make(**change).problems()
    assert any(fragment in line for line in found), found


def test_a_valid_fernet_key_and_matching_search_keys_are_accepted():
    from cryptography.fernet import Fernet

    settings = make(TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), GOOGLE_SEARCH_API_KEY="k", GOOGLE_SEARCH_CX="cx")
    assert settings.problems() == []


def test_every_problem_is_listed_at_once():
    settings = make(GROQ_API_KEY=None, COHERE_API_KEY=None, GENERATION_MODEL_ID=None, PRIMARY_LANG="fr")
    with pytest.raises(ConfigurationError) as exc:
        settings.ensure_valid()
    message = str(exc.value)
    assert message.startswith("Invalid configuration:")
    for fragment in ("GROQ_API_KEY", "COHERE_API_KEY", "GENERATION_MODEL_ID", "PRIMARY_LANG"):
        assert fragment in message  # the operator fixes everything in one go, not one error per restart


def test_languages_come_from_the_template_folders():
    assert {"en", "ar"} <= available_languages() and "__pycache__" not in available_languages()


# ── typos in the env file ────────────────────────────────────────────────────
def test_misspelt_names_in_an_env_file_are_reported(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GROQ_API_KY=abc\nGROQ_API_KEY=ok\nINPUT_DAFAULT_MAX_CHARACTERS=10\nrandom_other=1\n# COMMENT=1\n", encoding="utf-8")
    assert unknown_env_keys(env) == ["GROQ_API_KY", "random_other"]  # the old spellings are known, not typos
    assert unknown_env_keys(tmp_path / "missing.env") == []


def test_the_developers_own_env_file_has_no_unknown_names():
    assert unknown_env_keys() == []


# ── the example file stays in step with the settings ─────────────────────────
def _names_in_example():
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", (SRC / ".env.example").read_text(encoding="utf-8"), re.M))


def test_every_setting_is_documented_in_the_example_file():
    missing = sorted(set(Settings.model_fields) - _names_in_example())
    assert not missing, f"add these to src/.env.example: {missing}"


def test_the_example_file_lists_no_setting_that_does_not_exist():
    stale = sorted(_names_in_example() - setting_names())
    assert not stale, f"these are in src/.env.example but are not settings: {stale}"


def test_the_example_file_describes_a_valid_configuration_once_secrets_are_filled_in(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "k" * 40)
    settings = Settings(_env_file=SRC / ".env.example")
    # Only the API keys the operator must supply are missing.
    assert sorted(re.findall(r"[A-Z]+_API_KEY", " ".join(settings.problems()))) == ["COHERE_API_KEY", "GROQ_API_KEY"]


# ── one place reads the environment ──────────────────────────────────────────
def test_nothing_but_the_settings_module_reads_the_environment():
    offenders = []
    for path in SRC.rglob("*.py"):
        parts = set(path.relative_to(SRC).parts)
        if parts & {"tests", "__pycache__", "alembic"} or path.name.startswith("test_") or path.name == "config.py":
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"os\.getenv|os\.environ\.get|load_dotenv", line) or re.search(r"os\.environ\[[^\]]+\](?!\s*\+=)", line):
                offenders.append(f"{path.relative_to(SRC)}:{line_number}")
    assert not offenders, f"read configuration through get_settings(): {offenders}"


def test_every_setting_is_actually_used():
    """A setting nothing reads is a lie in the documentation; the check also catches a renamed setting
    whose old name is still used."""
    sources = {
        path: path.read_text(encoding="utf-8")
        for path in SRC.rglob("*.py")
        if not (set(path.relative_to(SRC).parts) & {"tests", "__pycache__", "alembic"})
        and not path.name.startswith("test_") and path.name != "config.py"
    }
    joined = "\n".join(sources.values())
    unused = sorted(name for name in Settings.model_fields if not re.search(rf"\b{name}\b", joined))
    assert not unused, f"settings nothing reads: {unused}"


# ── the settings reach the code that needs them ──────────────────────────────
def test_the_provider_factory_applies_the_configured_defaults():
    from stores.llm.LLMProviderFactory import LLMProviderFactory

    settings = make(GENERATION_DEFAULT_MAX_TOKENS=321, INPUT_DEFAULT_MAX_CHARACTERS=777, GENERATION_DEFAULT_TEMPERATURE=0.4,
                    GENERATION_MAX_INPUT_CHARACTERS=5000)
    provider = LLMProviderFactory(settings).create("GROQ")
    assert (provider.default_generation_max_output_tokens, provider.default_input_max_characters) == (321, 777)
    assert provider.default_generation_temperature == 0.4 and provider.max_prompt_characters == 5000


def test_web_search_uses_the_keys_from_the_settings(monkeypatch):
    from agent import AgentDeps
    from agent.nodes import web

    settings = get_settings()
    monkeypatch.setattr(settings, "GOOGLE_SEARCH_API_KEY", "search-key")
    monkeypatch.setattr(settings, "GOOGLE_SEARCH_CX", "engine-id")
    seen = {}

    class FakeResponse:
        def raise_for_status(self): ...
        def json(self):
            return {"items": [{"title": "T", "link": "https://example.com", "snippet": "S"}]}

    class FakeClient:
        def __init__(self, **kwargs): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

        async def get(self, url, params):
            seen.update(url=url, params=params)
            return FakeResponse()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=FakeClient))
    state = {"query": "weather", "metadata": {}, "chat_history": []}
    result = asyncio.run(web.web_search_node(state, AgentDeps(generation_client=None)))

    assert seen["params"]["key"] == "search-key" and seen["params"]["cx"] == "engine-id"
    assert result["status"] == "success" and "https://example.com" in result["result"]


def test_the_daily_summary_hour_is_configurable(monkeypatch):
    from services import notification_engine

    moment = datetime(2026, 6, 1, 5, 30, tzinfo=timezone.utc)  # 08:30 in Cairo
    monkeypatch.setattr(notification_engine, "local_now", lambda zone=None: moment.astimezone(ZoneInfo("Africa/Cairo")))

    class FakeUserModel:
        @classmethod
        async def create_instance(cls, db):
            return cls()

        async def list_google_users(self):
            return [{"user_id": "u1", "email": "a@x.com", "google_token": {"t": 1}, "timezone": "Africa/Cairo"}]

    monkeypatch.setattr(notification_engine, "UserModel", FakeUserModel)
    started = []

    async def fake_generate(self, user):
        started.append(user["user_id"])

    monkeypatch.setattr(notification_engine.NotificationEngine, "_generate_for_user", fake_generate)

    async def run(hour):
        monkeypatch.setattr(get_settings(), "DAILY_SUMMARY_HOUR", hour)
        engine = notification_engine.NotificationEngine(SimpleNamespace(mongo_db=object()))
        await engine.check_once()
        await asyncio.sleep(0)

    asyncio.run(run(10))
    assert started == []  # 08:30 is before a 10:00 briefing hour
    asyncio.run(run(8))
    assert started == ["u1"]


# ── the app refuses to start half-configured ─────────────────────────────────
def test_startup_stops_with_the_list_of_problems_before_opening_anything(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "GROQ_API_KEY", None)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_ID", None)

    def must_not_connect(*args, **kwargs):
        raise AssertionError("nothing may be opened before the configuration is checked")

    monkeypatch.setattr(main, "AsyncIOMotorClient", must_not_connect)

    async def attempt():
        async with main.lifespan(SimpleNamespace(state=SimpleNamespace())):
            pytest.fail("the app must not start")

    with pytest.raises(ConfigurationError) as exc:
        asyncio.run(attempt())
    assert "GROQ_API_KEY" in str(exc.value) and "EMBEDDING_MODEL_ID" in str(exc.value)
