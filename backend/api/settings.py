"""Settings endpoints: LLM configuration and connection test."""

from fastapi import APIRouter

from backend.api.deps import SettingsServiceDep
from backend.models.settings import ConnectionTestResult, SettingsRead, SettingsUpdate

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(service: SettingsServiceDep) -> SettingsRead:
    """Return the settings and whether an API key is stored. Never returns the key."""
    return await service.get()


@router.put("")
async def update_settings(data: SettingsUpdate, service: SettingsServiceDep) -> SettingsRead:
    """Update the settings; ``api_key: ""`` deletes the stored key."""
    return await service.update(data)


@router.post("/test")
async def test_connection(service: SettingsServiceDep) -> ConnectionTestResult:
    """Send a minimal completion with the saved settings."""
    return await service.test_connection()
