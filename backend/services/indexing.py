"""App-wide indexing jobs and live progress received from MCP."""

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel.ext.asyncio.session import AsyncSession

from backend.config import Settings
from backend.errors import DocterError
from backend.mcp_client import MCPError, MCPManager
from backend.models.project import IndexStatus, Project, ProjectIndexStatus
from backend.models.types import utcnow
from backend.services.projects import ProjectNotFoundError

logger = logging.getLogger(__name__)


class IndexAlreadyRunningError(DocterError):
    """The project already has an active indexing job."""

    status_code = 409
    code = "index_running"


class IndexingService:
    """Own indexing tasks for one app; never retain a request's database session."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession], mcp: MCPManager) -> None:
        self._sessions = sessions
        self._mcp = mcp
        self._tasks: dict[int, asyncio.Task[None]] = {}
        self._progress: dict[int, ProjectIndexStatus] = {}
        self._lock = asyncio.Lock()

    async def start(self, project_id: int) -> ProjectIndexStatus:
        """Start indexing, or reject a duplicate while its task is still running."""
        async with self._lock, self._sessions() as session:
            project = await self._get_project(session, project_id)
            task = self._tasks.get(project_id)
            if task is not None and not task.done():
                raise IndexAlreadyRunningError(
                    f"Indexing is already running for project {project_id}"
                )
            project.index_status = IndexStatus.RUNNING
            await session.commit()
            state = ProjectIndexStatus(
                status=IndexStatus.RUNNING,
                progress=0,
                message="Starting indexing",
                indexed_at=project.indexed_at,
            )
            self._progress[project_id] = state
            task = asyncio.create_task(
                self._run(project_id, project.path, state),
                name=f"docter-index-{project_id}",
            )
            self._tasks[project_id] = task
            task.add_done_callback(lambda finished: self._task_done(project_id, finished))
            return state.model_copy()

    async def get_status(self, project_id: int) -> ProjectIndexStatus:
        """Return live progress, falling back to the persisted project state."""
        async with self._sessions() as session:
            project = await self._get_project(session, project_id)
            state = self._progress.get(project_id)
            if state is not None:
                return state.model_copy()
            return ProjectIndexStatus(
                status=project.index_status,
                progress=100 if project.index_status == IndexStatus.DONE else 0,
                indexed_at=project.indexed_at,
            )

    async def close(self) -> None:
        """Cancel and await jobs before the MCP client and database are closed."""
        tasks = dict(self._tasks)
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for project_id, task in tasks.items():
            state = self._progress[project_id]
            if task.cancelled() and state.status == IndexStatus.RUNNING:
                # A task cancelled before its first step never enters _run's handler.
                state.error = "Indexing cancelled during shutdown"
                await self._finish(project_id, state, IndexStatus.FAILED)

    async def _run(self, project_id: int, path: str, state: ProjectIndexStatus) -> None:
        async def on_progress(progress: float, total: float | None, message: str | None) -> None:
            if total is not None and total > 0:
                state.progress = max(0, min(100, progress / total * 100))
            state.message = message

        try:
            result = await self._mcp.call_tool(
                "index_repo",
                {"path": path},
                timeout=Settings.INDEX_TIMEOUT_S,
                progress_callback=on_progress,
            )
            if result.is_error:
                raise MCPError(result.text or "MCP index_repo failed")
            await self._finish(project_id, state, IndexStatus.DONE)
        except asyncio.CancelledError:
            state.error = "Indexing cancelled during shutdown"
            logger.info("Indexing cancelled for project %s", project_id)
            await self._finish(project_id, state, IndexStatus.FAILED)
            raise
        except Exception as exc:
            # Background failures have no HTTP request to report through.
            logger.exception("Indexing failed for project %s", project_id)
            state.error = str(exc) or type(exc).__name__
            await self._finish(project_id, state, IndexStatus.FAILED)

    async def _finish(
        self, project_id: int, state: ProjectIndexStatus, status: IndexStatus
    ) -> None:
        async with self._sessions() as session:
            project = await session.get(Project, project_id)
            if project is not None:
                project.index_status = status
                if status == IndexStatus.DONE:
                    project.indexed_at = utcnow()
                await session.commit()
                state.indexed_at = project.indexed_at
        state.status = status
        if status == IndexStatus.DONE:
            state.progress = 100
            state.message = "Indexing complete"
        else:
            state.message = "Indexing failed"

    def _task_done(self, project_id: int, task: asyncio.Task[None]) -> None:
        if self._tasks.get(project_id) is task:
            del self._tasks[project_id]
        if not task.cancelled() and (exc := task.exception()) is not None:
            logger.error("Could not save indexing state for project %s", project_id, exc_info=exc)
            state = self._progress[project_id]
            state.status = IndexStatus.FAILED
            state.message = "Could not save indexing state"
            state.error = str(exc) or type(exc).__name__

    @staticmethod
    async def _get_project(session: AsyncSession, project_id: int) -> Project:
        project = await session.get(Project, project_id)
        if project is None:
            raise ProjectNotFoundError(f"Project {project_id} not found")
        return project
