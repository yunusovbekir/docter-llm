"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend.api import health
from backend.api import projects as projects_api
from backend.api import settings as settings_api
from backend.config import Settings, get_settings
from backend.db import create_all, create_engine, create_sessionmaker
from backend.errors import register_exception_handlers
from backend.llm import LLMGateway
from backend.llm.sampling import SamplingHandler
from backend.logging_config import configure_logging
from backend.mcp_client import MCPManager
from backend.services.indexing import IndexingService
from backend.services.secrets import ApiKeyStore
from backend.services.settings import SettingsService
from backend.static import SPAStaticFiles

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the Docter FastAPI application.

    Args:
        settings: Configuration to use; defaults to the environment-derived settings.
    """
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved.ensure_dirs()
        configure_logging(resolved)
        engine = create_engine(resolved.database_url)
        await create_all(engine)
        app.state.engine = engine
        sessionmaker = create_sessionmaker(engine)
        app.state.sessionmaker = sessionmaker

        async def gateway_factory() -> LLMGateway:
            async with sessionmaker() as session:
                return await SettingsService(session, ApiKeyStore()).gateway()

        mcp = MCPManager(
            resolved.mcp_command_args,
            errlog_path=resolved.logs_dir / resolved.MCP_SERVER_LOG_NAME,
            sampling_callback=SamplingHandler(gateway_factory),
        )
        app.state.mcp = mcp
        indexing = IndexingService(app.state.sessionmaker, mcp)
        app.state.indexing = indexing
        logger.info("Docter started (data_dir=%s)", resolved.data_dir)
        try:
            yield
        finally:
            await indexing.close()
            await mcp.close()
            await engine.dispose()
            logger.info("Docter stopped")

    app = FastAPI(title="Docter", version=__version__, lifespan=lifespan)
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(settings_api.router)
    app.include_router(projects_api.router)

    if resolved.frontend_dist_dir.is_dir():
        app.mount("/", SPAStaticFiles(resolved.frontend_dist_dir), name="frontend")

    return app
