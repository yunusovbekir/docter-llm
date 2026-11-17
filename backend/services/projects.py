"""Project service: register, list, and remove local git repositories."""

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.errors import DocterError
from backend.git.repository import RepositoryInfo, inspect_repository
from backend.models.project import Project, ProjectCreated, ProjectRead

logger = logging.getLogger(__name__)

DIRTY_WARNING = (
    "The working tree has uncommitted changes. Docter only commits to its own branch, "
    "but you may want to commit or stash these changes first."
)


class InvalidProjectPathError(DocterError):
    """The submitted path is not usable (e.g. not absolute)."""

    code = "invalid_path"


class ProjectPathNotFoundError(DocterError):
    """The submitted path does not exist."""

    code = "path_not_found"


class ProjectPathNotDirectoryError(DocterError):
    """The submitted path is not a directory."""

    code = "not_a_directory"


class ProjectAlreadyExistsError(DocterError):
    """A project with the same resolved path is already registered."""

    status_code = 409
    code = "project_exists"


class ProjectNotFoundError(DocterError):
    """No project has the requested id."""

    status_code = 404
    code = "project_not_found"


class ProjectService:
    """Manages registered projects. Never modifies files in the repositories."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, raw_path: str) -> ProjectCreated:
        """Validate ``raw_path`` as a git repository root and register it."""
        info = await asyncio.to_thread(_inspect_path, raw_path)
        path = str(info.root)

        existing = await self._session.exec(select(Project).where(Project.path == path))
        if existing.first() is not None:
            raise _already_exists(path)

        project = Project(
            name=info.root.name or path, path=path, current_branch=info.current_branch
        )
        self._session.add(project)
        try:
            await self._session.commit()
        except IntegrityError as exc:
            await self._session.rollback()
            raise _already_exists(path) from exc
        await self._session.refresh(project)
        logger.info("Registered project %s at %s", project.id, path)

        return ProjectCreated(
            **ProjectRead.model_validate(project).model_dump(),
            is_dirty=info.is_dirty,
            warnings=[DIRTY_WARNING] if info.is_dirty else [],
        )

    async def list_all(self) -> Sequence[ProjectRead]:
        """Return all projects, oldest first."""
        result = await self._session.exec(
            select(Project).order_by(col(Project.created_at), col(Project.id))
        )
        return [ProjectRead.model_validate(project) for project in result.all()]

    async def get(self, project_id: int) -> ProjectRead:
        """Return the project with ``project_id``."""
        return ProjectRead.model_validate(await self._get_row(project_id))

    async def delete(self, project_id: int) -> None:
        """Remove the project record. Files on disk are never touched."""
        project = await self._get_row(project_id)
        await self._session.delete(project)
        await self._session.commit()
        logger.info("Removed project %s (%s)", project_id, project.path)

    async def _get_row(self, project_id: int) -> Project:
        project = await self._session.get(Project, project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return project


def _inspect_path(raw_path: str) -> RepositoryInfo:
    return inspect_repository(_resolve_directory(raw_path))


def _resolve_directory(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        raise InvalidProjectPathError(f"Path must be absolute: {raw_path}")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ProjectPathNotFoundError(f"Path does not exist: {raw_path}") from exc
    if not resolved.is_dir():
        raise ProjectPathNotDirectoryError(f"Path is not a directory: {raw_path}")
    return resolved


def _already_exists(path: str) -> ProjectAlreadyExistsError:
    return ProjectAlreadyExistsError(f"A project is already registered at {path}")
