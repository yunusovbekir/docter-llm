"""Health check endpoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from backend import __version__

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    """Response body of ``GET /api/health``."""

    status: Literal["ok"]
    version: str


@router.get("/health")
async def health() -> HealthResponse:
    """Report that the service is up."""
    return HealthResponse(status="ok", version=__version__)
