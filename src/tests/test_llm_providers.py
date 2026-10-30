"""Provider behaviour: parameters, truncation limits and non-blocking async API (plan 2.7, 2.8, 2.18).
No network: the SDK clients are replaced with fakes."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stores.llm.LLMInterface import LLMInterface
from stores.llm.providers.CoHereProvider import CoHereProvider
from stores.llm.providers.GroqProvider import GroqProvider
from stores.llm.providers.OpenAIProvider import OpenAIProvider


def _chat_response(text="ok"):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class FakeChat:
    """Records the kwargs of each chat call, sync or async."""

    def __init__(self, delay=0.0):
        self.calls = []
        self.delay = delay
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _chat_response()

    async def acreate(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay)
        return _chat_response()


class AsyncFakeChat(FakeChat):
    async def create(self, **kwargs):  # type: ignore[override]
        return await self.acreate(**kwargs)


def _groq(**kw):
    provider = GroqProvider(api_key="x", default_input_max_characters=1024, default_generation_max_output_tokens=200, **kw)
    provider.set_generation_model("m")
    return provider


# ── 2.7 parameters ───────────────────────────────────────────────────────────
def test_groq_passes_temperature_and_max_tokens_including_zero():
    provider = _groq()
    provider.client = SimpleNamespace(chat=FakeChat())

    provider.generate_text("hi", temperature=0.0, max_output_tokens=50)
    provider.generate_text("hi")

    explicit, defaults = provider.client.chat.calls
    assert explicit["temperature"] == 0.0 and explicit["max_tokens"] == 50
    assert defaults["temperature"] == provider.default_generation_temperature
    assert defaults["max_tokens"] == 200


def test_missing_model_and_empty_response_are_handled():
    provider = GroqProvider(api_key="x")
    assert provider.generate_text("hi") is None  # no model set

    provider.set_generation_model("m")
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: SimpleNamespace(choices=[])
    )))
    assert provider.generate_text("hi") is None  # empty choices must not raise


def test_cohere_zero_temperature_is_respected():
    provider = CoHereProvider(api_key="x", default_generation_temperature=0.7)
    provider.set_generation_model("m")
    seen = {}

    def chat(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(text="ok")

    provider.client = SimpleNamespace(chat=chat)
    provider.generate_text("hi", temperature=0.0)
    assert seen["temperature"] == 0.0


# ── 2.8 truncation ───────────────────────────────────────────────────────────
def test_generation_prompts_are_not_clipped_to_the_embedding_limit():
    provider = _groq()  # embedding limit is 1024 characters
    long_instructions = "x" * 5000

    assert len(provider.construct_prompt(long_instructions, "system")["content"]) == 5000
    # Embedded text is still clipped to the embedding limit.
    assert len(provider.process_text("y" * 5000)) == 1024


def test_prompt_limit_is_configurable_and_logs_when_truncating(caplog):
    provider = _groq()
    provider.max_prompt_characters = 100

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        clipped = provider.construct_prompt("z" * 500, "user")["content"]

    assert len(clipped) == 100
    assert any("Truncating prompt" in record.message for record in caplog.records)


# ── 2.18 async API does not block the event loop ─────────────────────────────
def test_native_async_client_runs_calls_concurrently():
    provider = _groq()
    fake = AsyncFakeChat(delay=0.2)
    provider.async_client = SimpleNamespace(chat=fake)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        start = time.perf_counter()
        results = await asyncio.gather(*[provider.agenerate_text(f"q{i}") for i in range(5)])
        elapsed = time.perf_counter() - start
        task.cancel()
        return results, elapsed, ticks

    results, elapsed, ticks = asyncio.run(scenario())
    assert results == ["ok"] * 5 and len(fake.calls) == 5
    assert elapsed < 0.6  # five 0.2s calls overlapped (sequential would be 1.0s)
    assert ticks > 10  # the loop kept running while waiting


def test_providers_without_an_async_client_fall_back_to_a_worker_thread():
    class SyncOnlyProvider(LLMInterface):
        default_generation_max_output_tokens = 10
        default_generation_temperature = 0.0

        def set_generation_model(self, model_id): ...
        def set_embedding_model(self, model_id, embedding_size): ...
        def embed_text(self, text, document_type=None): return [1.0]
        def embed_texts(self, texts, document_type=None): return [[1.0] for _ in texts]
        def construct_prompt(self, prompt, role): return {"role": role, "content": prompt}

        def generate_text(self, prompt, chat_history=None, max_output_tokens=None, temperature=None):
            time.sleep(0.2)  # blocking work, as a sync SDK call would be
            return prompt.upper()

    provider = SyncOnlyProvider()

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        start = time.perf_counter()
        results = await asyncio.gather(*[provider.agenerate_text(f"q{i}") for i in range(4)])
        elapsed = time.perf_counter() - start
        task.cancel()
        vectors = await provider.aembed_texts(["a", "b"])
        return results, elapsed, ticks, vectors

    results, elapsed, ticks, vectors = asyncio.run(scenario())
    assert results == ["Q0", "Q1", "Q2", "Q3"]
    assert elapsed < 0.6 and ticks > 10
    assert vectors == [[1.0], [1.0]]


def test_openai_async_embeddings_keep_input_order():
    provider = OpenAIProvider(api_key="x")
    provider.set_embedding_model("e", 3)

    async def create(**kwargs):
        # The API may return items out of order; the provider must restore the input order.
        return SimpleNamespace(data=[
            SimpleNamespace(index=1, embedding=[2.0]),
            SimpleNamespace(index=0, embedding=[1.0]),
        ])

    provider.async_client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    assert asyncio.run(provider.aembed_texts(["first", "second"])) == [[1.0], [2.0]]


def test_cohere_async_embeddings_are_batched():
    provider = CoHereProvider(api_key="x")
    provider.set_embedding_model("e", 3)
    batch_sizes = []

    async def embed(texts, **kwargs):
        batch_sizes.append(len(texts))
        return SimpleNamespace(embeddings=SimpleNamespace(float=[[0.0]] * len(texts)))

    provider.async_client = SimpleNamespace(embed=embed)
    vectors = asyncio.run(provider.aembed_texts([f"t{i}" for i in range(100)]))
    assert len(vectors) == 100 and batch_sizes == [96, 4]


# ── nodes await the async API ────────────────────────────────────────────────
def test_data_analysis_node_awaits_agenerate_text(tmp_path, monkeypatch):
    from controllers import data_analysis_node as dan

    csv_path = tmp_path / "sales.csv"
    csv_path.write_text("Region,Sales\na,10\na,5\nb,3\n")
    monkeypatch.setattr(dan, "resolve_user_file", lambda state: str(csv_path))

    replies = iter([
        '{"op": "aggregate", "column": "Sales", "agg": "sum", "group_by": "Region", "chart": "bar"}',
        "Region a leads with 15.",
    ])

    class AsyncOnlyClient:  # has no sync generate_text: the node must use the async API
        async def agenerate_text(self, **kwargs):
            return next(replies)

    state = {"query": "sales by region", "metadata": {}}
    result = asyncio.run(dan.data_analysis_node(state, generation_client=AsyncOnlyClient()))

    assert result["status"] == "success"
    assert result["result"] == "Region a leads with 15."
    assert result["chart_data"]["type"] == "bar"
    assert {row["Region"]: row["Sales"] for row in result["chart_data"]["data"]} == {"a": 15, "b": 3}
