"""Project endpoints: register, list, get, and remove local repositories."""

from collections.abc import Sequence

from fastapi import APIRouter, status

from backend.api.deps import IndexingServiceDep, ProjectServiceDep
from backend.models.project import ProjectCreate, ProjectCreated, ProjectIndexStatus, ProjectRead

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(data: ProjectCreate, service: ProjectServiceDep) -> ProjectCreated:
    """Register a local git repository. A dirty working tree is reported, not rejected."""
    return await service.create(data.path)


@router.get("")
async def list_projects(service: ProjectServiceDep) -> Sequence[ProjectRead]:
    """List all registered projects."""
    return await service.list_all()


@router.get("/{project_id}")
async def get_project(project_id: int, service: ProjectServiceDep) -> ProjectRead:
    """Return a single project."""
    return await service.get(project_id)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: int, service: ProjectServiceDep) -> None:
    """Remove the project record only; the repository on disk is left untouched."""
    await service.delete(project_id)


@router.post("/{project_id}/index", status_code=status.HTTP_202_ACCEPTED)
async def start_indexing(project_id: int, service: IndexingServiceDep) -> ProjectIndexStatus:
    """Start a background indexing job for this project."""
    return await service.start(project_id)


@router.get("/{project_id}/index/status")
async def get_index_status(project_id: int, service: IndexingServiceDep) -> ProjectIndexStatus:
    """Return indexing state and live MCP progress."""
    return await service.get_status(project_id)
