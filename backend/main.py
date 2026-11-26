"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend import __version__
from backend.api import changes as changes_api
from backend.api import health
from backend.api import projects as projects_api
from backend.api import sessions as sessions_api
from backend.api import settings as settings_api
from backend.config import Settings, get_settings
from backend.db import create_all, create_engine, create_sessionmaker, redact_postgres_errors
from backend.errors import ErrorResponse, register_exception_handlers
from backend.git.service import GitService
from backend.llm import LLMGateway
from backend.llm.sampling import SamplingHandler
from backend.logging_config import configure_logging
from backend.mcp_client import MCPManager
from backend.middleware import RequestIDMiddleware
from backend.services.indexing import IndexingService
from backend.services.review import ReviewService
from backend.services.secrets import ApiKeyStore
from backend.services.sessions import SessionService
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
        async with AsyncExitStack() as resources:
            resources.push_async_callback(engine.dispose)
            sessionmaker = create_sessionmaker(engine)
            app.state.engine = engine
            app.state.sessionmaker = sessionmaker
            app.state.git = GitService(sessionmaker, resolved.worktrees_dir)

            async def gateway_factory() -> LLMGateway:
                async with sessionmaker() as session:
                    return await SettingsService(session, ApiKeyStore()).gateway()

            mcp = MCPManager(
                resolved.mcp_command_args,
                env=resolved.mcp_env,
                errlog_path=resolved.logs_dir / resolved.MCP_SERVER_LOG_NAME,
                sampling_callback=SamplingHandler(gateway_factory),
            )
            resources.push_async_callback(mcp.close)
            app.state.mcp = mcp
            indexing = IndexingService(sessionmaker, mcp)
            resources.push_async_callback(indexing.close)
            app.state.indexing = indexing
            sessions = SessionService(sessionmaker, mcp)
            resources.push_async_callback(sessions.close)
            app.state.sessions = sessions
            app.state.review = ReviewService(sessionmaker, app.state.git, sessions)
            with redact_postgres_errors(engine):
                await create_all(engine)
                await indexing.recover()
                await sessions.recover()
            logger.info("Docter started (data_dir=%s)", resolved.data_dir)
            yield
        logger.info("Docter stopped")

    app = FastAPI(
        title="Docter",
        version=__version__,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "health", "description": "Backend availability and version."},
            {"name": "settings", "description": "LLM configuration and connection testing."},
            {"name": "projects", "description": "Local repositories and indexing jobs."},
            {
                "name": "sessions",
                "description": "Conversations, streamed agent runs, and proposals.",
            },
            {"name": "changes", "description": "Human review and approval of proposed patches."},
        ],
        responses={
            status: {"model": ErrorResponse} for status in (400, 404, 409, 422, 500, 502, 503, 504)
        },
    )
    app.state.settings = resolved

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(settings_api.router)
    app.include_router(projects_api.router)
    app.include_router(sessions_api.router)
    app.include_router(changes_api.router)

    if resolved.frontend_dist_dir.is_dir():
        app.mount("/", SPAStaticFiles(resolved.frontend_dist_dir), name="frontend")

    return app
