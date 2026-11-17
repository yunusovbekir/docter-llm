import logging

from httpx import AsyncClient

from backend.config import Settings
from backend.logging_config import LOG_FILE_NAME


async def test_logs_are_written_to_logs_dir(settings: Settings, client: AsyncClient) -> None:
    logging.getLogger("backend.test").warning("hello from the test")
    for handler in logging.getLogger().handlers:
        handler.flush()

    content = (settings.logs_dir / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Docter started" in content
    assert "hello from the test" in content
