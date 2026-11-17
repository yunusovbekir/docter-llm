"""``docter`` console script."""

import uvicorn

from backend.config import get_settings
from backend.logging_config import configure_logging


def main() -> None:
    """Run the Docter backend with uvicorn."""
    settings = get_settings()
    settings.ensure_dirs()
    configure_logging(settings)
    uvicorn.run(
        "backend.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
