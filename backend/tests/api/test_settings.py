import asyncio
from typing import Any

import keyring
import litellm
import pytest
from httpx import AsyncClient
from keyring.backends.fail import Keyring as FailKeyring

from backend.config import Settings
from backend.models.settings import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MAX_AGENT_STEPS,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    DEFAULT_TEMPERATURE,
)
from backend.services import settings as settings_service
from backend.services.secrets import KEYRING_SERVICE
from backend.tests.fakes import InMemoryKeyring

API_KEY = "sk-super-secret-test-key"


class FakeCompletion:
    """Stand-in for ``litellm.acompletion`` that records calls."""

    def __init__(self, error: Exception | None = None, delay_s: float = 0.0) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error
        self._delay_s = delay_s

    async def __call__(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._error is not None:
            raise self._error
        return object()


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeCompletion) -> FakeCompletion:
    monkeypatch.setattr(litellm, "acompletion", fake)
    return fake


async def test_get_returns_defaults_without_api_key(client: AsyncClient) -> None:
    response = await client.get("/api/settings")

    assert response.status_code == 200
    assert response.json() == {
        "provider": DEFAULT_PROVIDER,
        "model": DEFAULT_MODEL,
        "base_url": None,
        "embedding_model": DEFAULT_EMBEDDING_MODEL,
        "temperature": DEFAULT_TEMPERATURE,
        "max_agent_steps": DEFAULT_MAX_AGENT_STEPS,
        "has_api_key": False,
    }
    assert DEFAULT_MAX_AGENT_STEPS == 25


async def test_put_updates_and_persists_settings(client: AsyncClient) -> None:
    payload = {
        "provider": " Anthropic ",
        "model": "claude-sonnet-4-5",
        "base_url": "https://llm.example.com/v1",
        "embedding_model": "BAAI/bge-base-en-v1.5",
        "temperature": 0.7,
        "max_agent_steps": 40,
    }

    response = await client.put("/api/settings", json=payload)

    assert response.status_code == 200
    expected = {**payload, "provider": "anthropic", "has_api_key": False}
    assert response.json() == expected
    assert (await client.get("/api/settings")).json() == expected


async def test_put_is_partial(client: AsyncClient) -> None:
    await client.put("/api/settings", json={"model": "gpt-4.1", "max_agent_steps": 10})

    response = await client.put("/api/settings", json={"temperature": 1.0})

    body = response.json()
    assert body["model"] == "gpt-4.1"
    assert body["max_agent_steps"] == 10
    assert body["temperature"] == 1.0


async def test_put_with_api_key_stores_it_only_in_keyring(
    client: AsyncClient, settings: Settings, memory_keyring: InMemoryKeyring
) -> None:
    response = await client.put("/api/settings", json={"provider": "openai", "api_key": API_KEY})

    assert response.status_code == 200
    assert response.json()["has_api_key"] is True
    assert API_KEY not in response.text
    assert memory_keyring.passwords == {(KEYRING_SERVICE, "openai"): API_KEY}
    assert API_KEY.encode() not in settings.db_path.read_bytes()

    get_response = await client.get("/api/settings")
    assert get_response.json()["has_api_key"] is True
    assert API_KEY not in get_response.text


async def test_put_without_api_key_keeps_existing_key(
    client: AsyncClient, memory_keyring: InMemoryKeyring
) -> None:
    await client.put("/api/settings", json={"api_key": API_KEY})

    response = await client.put("/api/settings", json={"model": "gpt-4.1", "api_key": None})

    assert response.json()["has_api_key"] is True
    assert memory_keyring.passwords == {(KEYRING_SERVICE, DEFAULT_PROVIDER): API_KEY}


async def test_put_empty_api_key_deletes_it(
    client: AsyncClient, memory_keyring: InMemoryKeyring
) -> None:
    await client.put("/api/settings", json={"api_key": API_KEY})

    response = await client.put("/api/settings", json={"api_key": ""})

    assert response.status_code == 200
    assert response.json()["has_api_key"] is False
    assert memory_keyring.passwords == {}


async def test_api_key_is_scoped_to_provider(
    client: AsyncClient, memory_keyring: InMemoryKeyring
) -> None:
    await client.put("/api/settings", json={"provider": "openai", "api_key": "sk-openai"})

    switched = await client.put("/api/settings", json={"provider": "anthropic"})
    assert switched.json()["has_api_key"] is False

    stored = await client.put("/api/settings", json={"api_key": "sk-anthropic"})
    assert stored.json()["has_api_key"] is True
    assert memory_keyring.passwords == {
        (KEYRING_SERVICE, "openai"): "sk-openai",
        (KEYRING_SERVICE, "anthropic"): "sk-anthropic",
    }


async def test_blank_or_null_base_url_clears_it(client: AsyncClient) -> None:
    await client.put("/api/settings", json={"base_url": "http://localhost:11434"})

    assert (await client.put("/api/settings", json={"base_url": ""})).json()["base_url"] is None

    await client.put("/api/settings", json={"base_url": "http://localhost:11434"})
    assert (await client.put("/api/settings", json={"base_url": None})).json()["base_url"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"temperature": 2.5},
        {"max_agent_steps": 0},
        {"provider": ""},
        {"provider": "open ai"},
        {"provider": None},
        {"model": "   "},
        {"base_url": "not-a-url"},
        {"base_url": "ftp://example.com"},
        {"unknown": 1},
    ],
)
async def test_put_rejects_invalid_payload(client: AsyncClient, payload: dict[str, Any]) -> None:
    response = await client.put("/api/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_validation_error_never_echoes_api_key(client: AsyncClient) -> None:
    response = await client.put("/api/settings", json={"temperature": 9, "api_key": API_KEY})

    assert response.status_code == 422
    assert API_KEY not in response.text


async def test_put_api_key_without_keyring_returns_503_and_saves_nothing(
    client: AsyncClient,
) -> None:
    keyring.set_keyring(FailKeyring())  # type: ignore[no-untyped-call]

    response = await client.put("/api/settings", json={"model": "gpt-4.1", "api_key": API_KEY})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "keyring_unavailable"
    body = (await client.get("/api/settings")).json()
    assert body["model"] == DEFAULT_MODEL
    assert body["has_api_key"] is False


async def test_connection_success_sends_minimal_completion(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, FakeCompletion())
    await client.put(
        "/api/settings",
        json={
            "provider": "ollama",
            "model": "llama3.1",
            "base_url": "http://localhost:11434",
            "api_key": API_KEY,
        },
    )

    response = await client.post("/api/settings/test")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["error"] is None
    assert isinstance(body["latency_ms"], int)
    assert body["latency_ms"] >= 0
    [call] = fake.calls
    assert call["model"] == "llama3.1"
    assert call["custom_llm_provider"] == "ollama"
    assert call["base_url"] == "http://localhost:11434"
    assert call["api_key"] == API_KEY
    assert call["max_tokens"] == 1
    assert call["timeout"] == 15
    assert len(call["messages"]) == 1


async def test_connection_without_key_passes_none(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _install(monkeypatch, FakeCompletion())

    await client.post("/api/settings/test")

    assert fake.calls[0]["api_key"] is None
    assert fake.calls[0]["base_url"] is None


async def test_connection_failure_reports_redacted_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(monkeypatch, FakeCompletion(error=RuntimeError(f"Invalid API key: {API_KEY}")))
    await client.put("/api/settings", json={"api_key": API_KEY})

    response = await client.post("/api/settings/test")

    assert response.status_code == 200
    assert response.json() == {"ok": False, "latency_ms": None, "error": "Invalid API key: ***"}


async def test_connection_timeout(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_service, "CONNECTION_TEST_TIMEOUT_S", 0.05)
    _install(monkeypatch, FakeCompletion(delay_s=5))

    response = await client.post("/api/settings/test")

    assert response.json() == {"ok": False, "latency_ms": None, "error": "Timed out after 0.05s"}
