"""Settings service: persisted LLM configuration plus the keyring-backed API key."""

import asyncio
import logging
import time

from sqlmodel.ext.asyncio.session import AsyncSession

from backend.models.settings import (
    SETTINGS_ROW_ID,
    AppSettings,
    ConnectionTestResult,
    SettingsRead,
    SettingsUpdate,
)
from backend.services.secrets import ApiKeyStore

logger = logging.getLogger(__name__)

CONNECTION_TEST_TIMEOUT_S = 15.0
_MAX_ERROR_LENGTH = 500


class SettingsService:
    """Reads, updates, and tests the application's LLM settings."""

    def __init__(self, session: AsyncSession, keys: ApiKeyStore) -> None:
        self._session = session
        self._keys = keys

    async def get(self) -> SettingsRead:
        """Return the current settings (defaults if never saved)."""
        return await self._to_read(await self._load())

    async def update(self, data: SettingsUpdate) -> SettingsRead:
        """Apply the fields set in ``data`` and store or delete the API key."""
        row = await self._session.get(AppSettings, SETTINGS_ROW_ID)
        if row is None:
            row = AppSettings()
            self._session.add(row)

        provider = data.provider if data.provider is not None else row.provider
        if data.api_key is not None:
            secret = data.api_key.get_secret_value()
            if secret:
                await self._keys.set(provider, secret)
            else:
                await self._keys.delete(provider)

        for name, value in data.model_dump(exclude_unset=True, exclude={"api_key"}).items():
            setattr(row, name, value)
        await self._session.commit()
        await self._session.refresh(row)
        logger.info("Settings updated (provider=%s, model=%s)", row.provider, row.model)
        return await self._to_read(row)

    async def test_connection(self) -> ConnectionTestResult:
        """Send a minimal completion with the saved settings and report the outcome."""
        row = await self._load()
        api_key = await self._keys.get(row.provider)

        import litellm  # imported lazily: the import is slow and only needed here

        started = time.perf_counter()
        try:
            async with asyncio.timeout(CONNECTION_TEST_TIMEOUT_S):
                await litellm.acompletion(
                    model=row.model,
                    custom_llm_provider=row.provider,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                    timeout=CONNECTION_TEST_TIMEOUT_S,
                    api_key=api_key,
                    base_url=row.base_url,
                )
        except TimeoutError:
            return ConnectionTestResult(
                ok=False,
                latency_ms=None,
                error=f"Timed out after {CONNECTION_TEST_TIMEOUT_S:g}s",
            )
        except Exception as exc:
            logger.info(
                "LLM connection test failed (provider=%s): %s", row.provider, type(exc).__name__
            )
            return ConnectionTestResult(
                ok=False, latency_ms=None, error=_safe_error_message(exc, api_key)
            )
        latency_ms = round((time.perf_counter() - started) * 1000)
        return ConnectionTestResult(ok=True, latency_ms=latency_ms, error=None)

    async def _load(self) -> AppSettings:
        return await self._session.get(AppSettings, SETTINGS_ROW_ID) or AppSettings()

    async def _to_read(self, row: AppSettings) -> SettingsRead:
        return SettingsRead(
            provider=row.provider,
            model=row.model,
            base_url=row.base_url,
            embedding_model=row.embedding_model,
            temperature=row.temperature,
            max_agent_steps=row.max_agent_steps,
            has_api_key=await self._keys.has(row.provider),
        )


def _safe_error_message(exc: Exception, api_key: str | None) -> str:
    message = str(exc) or type(exc).__name__
    if api_key:
        message = message.replace(api_key, "***")
    return message[:_MAX_ERROR_LENGTH]
