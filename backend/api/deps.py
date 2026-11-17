"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, Request
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.db import get_session
from backend.mcp_client import MCPManager
from backend.services.projects import ProjectService
from backend.services.secrets import ApiKeyStore
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
