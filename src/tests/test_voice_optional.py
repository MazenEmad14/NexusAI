"""Speech-to-text is an optional extra: absent -> 503, present -> loaded lazily, once (plan 2.20)."""
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers.auth_utils import get_current_user
from routes import agent as agent_routes


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(agent_routes.agent_router)
    app.dependency_overrides[get_current_user] = lambda: {"_id": "u1", "email": "a@example.com"}
    monkeypatch.setattr(agent_routes, "_whisper_model", None)
    return TestClient(app, raise_server_exceptions=False)


def _post_audio(client, data=b"RIFF....WAVE"):
    return client.post("/api/v1/agent/stt", files={"file": ("a.wav", io.BytesIO(data), "audio/wav")})


def test_without_whisper_installed_stt_answers_503_not_a_crash(client, monkeypatch):
    monkeypatch.setattr(agent_routes, "whisper", None)
    response = _post_audio(client)
    assert response.status_code == 503 and "not installed" in response.json()["error"]


def test_the_model_is_loaded_on_first_use_only_and_once(client, monkeypatch):
    loads = []

    class FakeModel:
        def transcribe(self, path):
            return {"text": "hello world"}

    fake_whisper = SimpleNamespace(load_model=lambda name: loads.append(name) or FakeModel())
    monkeypatch.setattr(agent_routes, "whisper", fake_whisper)
    assert loads == []  # importing the routes loaded nothing

    assert _post_audio(client).json() == {"text": "hello world"}
    assert _post_audio(client).json() == {"text": "hello world"}
    assert loads == ["base"]  # a single load for two requests


def test_a_failing_transcription_is_a_generic_500(client, monkeypatch):
    class BrokenModel:
        def transcribe(self, path):
            raise RuntimeError("ffmpeg not found at C:\\secret\\path")

    monkeypatch.setattr(agent_routes, "whisper", SimpleNamespace(load_model=lambda name: BrokenModel()))
    response = _post_audio(client)
    assert response.status_code == 500 and "secret" not in response.text


def test_oversized_audio_is_rejected_before_transcribing(client, monkeypatch):
    monkeypatch.setattr(agent_routes, "MAX_STT_BYTES", 10)
    monkeypatch.setattr(agent_routes, "whisper", SimpleNamespace(load_model=lambda name: pytest.fail("must not load")))
    assert _post_audio(client, b"x" * 100).status_code == 413
