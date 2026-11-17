"""Provider API keys stored in the OS keyring, never in the database."""

import asyncio
import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from backend.errors import DocterError

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "docter"


class KeyringUnavailableError(DocterError):
    """Raised when no usable OS keyring backend is available for writing."""

    status_code = 503
    code = "keyring_unavailable"


class ApiKeyStore:
    """Reads and writes API keys under keyring service ``docter``, username = provider.

    Reads degrade gracefully (a missing keyring means "no key"); writes raise
    ``KeyringUnavailableError`` so the user is told the key was not saved.
    """

    def __init__(self, service: str = KEYRING_SERVICE) -> None:
        self._service = service

    async def get(self, provider: str) -> str | None:
        """Return the key for ``provider``, or ``None`` if absent or unreadable."""
        try:
            return await asyncio.to_thread(keyring.get_password, self._service, provider)
        except KeyringError as exc:
            logger.warning("Cannot read API key from keyring: %s", type(exc).__name__)
            return None

    async def has(self, provider: str) -> bool:
        """Return whether a key is stored for ``provider``."""
        return bool(await self.get(provider))

    async def set(self, provider: str, api_key: str) -> None:
        """Store ``api_key`` for ``provider``, replacing any existing key."""
        try:
            await asyncio.to_thread(keyring.set_password, self._service, provider, api_key)
        except KeyringError as exc:
            raise _unavailable() from exc

    async def delete(self, provider: str) -> None:
        """Delete the key for ``provider``; a missing key is not an error."""
        try:
            await asyncio.to_thread(keyring.delete_password, self._service, provider)
        except PasswordDeleteError:
            return
        except KeyringError as exc:
            raise _unavailable() from exc


def _unavailable() -> KeyringUnavailableError:
    return KeyringUnavailableError(
        "No usable OS keyring is available, so the API key was not saved. "
        "Install and unlock a keyring backend (e.g. GNOME Keyring, KWallet, macOS Keychain, "
        "Windows Credential Locker) and try again."
    )
