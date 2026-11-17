from collections.abc import Iterator

import keyring
import pytest
from keyring.backends.fail import Keyring as FailKeyring

from backend.services.secrets import KEYRING_SERVICE, ApiKeyStore, KeyringUnavailableError
from backend.tests.fakes import InMemoryKeyring


@pytest.fixture
def no_keyring() -> Iterator[None]:
    keyring.set_keyring(FailKeyring())  # type: ignore[no-untyped-call]
    yield


async def test_set_get_and_delete_use_docter_service_and_provider_username(
    memory_keyring: InMemoryKeyring,
) -> None:
    store = ApiKeyStore()

    await store.set("openai", "sk-test")

    assert memory_keyring.passwords == {(KEYRING_SERVICE, "openai"): "sk-test"}
    assert await store.get("openai") == "sk-test"
    assert await store.has("openai")
    assert not await store.has("anthropic")

    await store.delete("openai")

    assert memory_keyring.passwords == {}
    assert await store.get("openai") is None


async def test_delete_missing_key_is_noop() -> None:
    await ApiKeyStore().delete("openai")


@pytest.mark.usefixtures("no_keyring")
async def test_reads_degrade_without_keyring() -> None:
    store = ApiKeyStore()

    assert await store.get("openai") is None
    assert not await store.has("openai")


@pytest.mark.usefixtures("no_keyring")
async def test_writes_fail_without_keyring() -> None:
    store = ApiKeyStore()

    with pytest.raises(KeyringUnavailableError):
        await store.set("openai", "sk-test")
    with pytest.raises(KeyringUnavailableError):
        await store.delete("openai")
