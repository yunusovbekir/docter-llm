"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db import get_session
from backend.git.service import GitService
from backend.mcp_client import MCPManager
from backend.services.indexing import IndexingService
from backend.services.projects import ProjectService
from backend.services.review import ReviewService
from backend.services.secrets import ApiKeyStore
from backend.services.sessions import SessionService
from backend.services.settings import SettingsService

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_api_key_store() -> ApiKeyStore:
    """Provide the keyring-backed API key store."""
    return ApiKeyStore()


ApiKeyStoreDep = Annotated[ApiKeyStore, Depends(get_api_key_store)]


def get_settings_service(session: SessionDep, keys: ApiKeyStoreDep) -> SettingsService:
    """Provide a ``SettingsService`` bound to the request's session."""
    return SettingsService(session, keys)


SettingsServiceDep = Annotated[SettingsService, Depends(get_settings_service)]


def get_project_service(session: SessionDep) -> ProjectService:
    """Provide a ``ProjectService`` bound to the request's session."""
    return ProjectService(session)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


def get_mcp_manager(request: Request) -> MCPManager:
    """Provide the app-wide ``MCPManager`` created in the lifespan."""
    manager: MCPManager = request.app.state.mcp
    return manager


MCPManagerDep = Annotated[MCPManager, Depends(get_mcp_manager)]


def get_indexing_service(request: Request) -> IndexingService:
    """Provide the app-wide indexing task registry."""
    service: IndexingService = request.app.state.indexing
    return service


IndexingServiceDep = Annotated[IndexingService, Depends(get_indexing_service)]


def get_session_service(request: Request) -> SessionService:
    """Provide the app-wide session service and chat worker registry."""
    service: SessionService = request.app.state.sessions
    return service


SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]


def get_git_service(request: Request) -> GitService:
    """Provide the app-wide Git service and per-session approval locks."""
    service: GitService = request.app.state.git
    return service


GitServiceDep = Annotated[GitService, Depends(get_git_service)]


def get_review_service(request: Request) -> ReviewService:
    """Provide app-wide review decisions and per-session review locks."""
    service: ReviewService = request.app.state.review
    return service


ReviewServiceDep = Annotated[ReviewService, Depends(get_review_service)]
