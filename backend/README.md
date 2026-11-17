# Docter Backend

FastAPI backend for Docter. It hosts the agent, acts as the MCP client, stores state in SQLite, and serves the built Vue UI.

See [`../docs/README.md`](../docs/README.md) for the overall project plan and [`../docs/backend_features.md`](../docs/backend_features.md) for the backend roadmap.

## Requirements

- Python 3.12
- [uv](https://docs.astral.sh/uv/)

## Quick start

Run these commands from the repository root:

```bash
uv sync          # create .venv and install dependencies
uv run docter  # start the server on http://127.0.0.1:8000
```

To check that the server is running:

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","version":"0.1.0"}
```

Interactive API docs are available at http://127.0.0.1:8000/docs.

## Configuration

Settings are read from environment variables with the `DOCTER_` prefix (see `config.py`).

| Variable                     | Default                        | Description                                         |
|------------------------------|--------------------------------|-----------------------------------------------------|
| `DOCTER_DATA_DIR`          | `~/.docter`                  | Root directory for all local data                   |
| `DOCTER_HOST`              | `127.0.0.1`                    | Bind address                                        |
| `DOCTER_PORT`              | `8000`                         | Bind port                                           |
| `DOCTER_LOG_LEVEL`         | `INFO`                         | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`  |
| `DOCTER_CORS_ORIGINS`      | `["http://localhost:5173"]`    | Allowed CORS origins, given as a JSON list          |
| `DOCTER_FRONTEND_DIST_DIR` | `<repo>/frontend/dist`         | Directory containing the built Vue app              |

### Data directory layout

These paths are derived from `data_dir`. They are created at startup.

```
~/.docter/
  docter.db     # SQLite database
  index/          # vector indexes
  worktrees/      # git worktrees for sessions
  logs/           # docter.log (rotating, 5 MB x 3)
```

## Architecture

| Module              | Responsibility                                                                 |
|---------------------|--------------------------------------------------------------------------------|
| `main.py`           | `create_app()` factory, lifespan, CORS, routers, and static UI mount           |
| `cli.py`            | `docter` console script that runs uvicorn                                    |
| `config.py`         | `Settings` (pydantic-settings) and derived paths                               |
| `db.py`             | Async SQLModel engine, `create_all`, and the `get_session` dependency          |
| `errors.py`         | `DocterError` base class and the global exception handlers                   |
| `logging_config.py` | Stdlib logging to the console and `logs_dir`                                   |
| `static.py`         | `SPAStaticFiles`: serves `frontend/dist` and falls back to `index.html`        |
| `api/`              | HTTP routers (`health.py`, `settings.py`, `projects.py`) and shared dependencies (`deps.py`) |
| `models/`           | SQLModel tables, the separate Pydantic request/response schemas, and `UTCDateTime` |
| `services/`         | Business logic (`settings.py`, `projects.py`) and the keyring-backed `ApiKeyStore` (`secrets.py`) |
| `git/`              | GitPython helpers (`repository.py`: read-only working-tree inspection)         |

### Lifespan

At startup the app creates the data directories, configures logging, creates the async engine, runs `create_all`, and stores `engine` and `sessionmaker` on `app.state`. At shutdown it disposes of the engine.

### Database sessions

Use the dependency in routes or services:

```python
from typing import Annotated
from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession
from backend.db import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
```

### Errors

Every error response has the same JSON shape:

```json
{"error": {"code": "not_found", "message": "Not Found"}}
```

- **Domain errors:** subclass `DocterError` and set `status_code` and `code`.
- **HTTP and validation errors:** mapped to the same shape. Validation errors use the `validation_error` code.
- **Unhandled exceptions:** logged with a traceback. The client receives a 500 response with `internal_error` and no internal details.

### Frontend serving

If `frontend_dist_dir` exists, it is mounted at `/`:

- Unknown non-API paths fall back to `index.html` so Vue Router can handle them.
- `/api/*` paths never fall back. Unknown API routes return a JSON 404.

During development, run the Vite dev server on `http://localhost:5173` instead. CORS allows that origin.

## LLM settings

The LLM configuration is stored as a single row in the `app_settings` table. It has these fields: `provider`, `model`, `base_url` (optional), `embedding_model`, `temperature` (0–2), and `max_agent_steps` (1–200, default 25).

The **API key is stored only in the OS keyring**, under service `docter` with the provider as the username. It is never written to the database, returned by the API, or logged.

| Endpoint                   | Description                                                                              |
|----------------------------|------------------------------------------------------------------------------------------|
| `GET /api/settings`        | Returns the settings plus `has_api_key`. Defaults apply if nothing has been saved yet.  |
| `PUT /api/settings`        | Partial update. Omitted fields are unchanged; `base_url: ""` or `null` clears it.        |
| `POST /api/settings/test`  | Sends a 1-token LiteLLM completion with a 15 s timeout and returns `{ok, latency_ms, error}`. |

`api_key` handling in `PUT /api/settings`:

- **Omitted or `null`:** the stored key is unchanged.
- **`""`:** the key for the provider is deleted.
- **Any other value:** the key is stored for the provider (the new provider, if `provider` changes in the same request).

Example:

```bash
curl -X PUT http://127.0.0.1:8000/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"provider": "ollama", "model": "llama3.1", "base_url": "http://localhost:11434"}'
curl -X POST http://127.0.0.1:8000/api/settings/test
# {"ok": true, "latency_ms": 412, "error": null}
```

The connection test uses the **saved** settings. A failure still returns HTTP 200 with `ok: false`. The error message is truncated, and the API key is masked if the provider echoes it.

### Keyring availability

Docter uses the `keyring` package, which supports macOS Keychain, Windows Credential Locker, GNOME Keyring/Secret Service, and KWallet. If no backend is available (for example in WSL or on a headless server):

- `has_api_key` is reported as `false`.
- Saving or deleting a key returns `503` with `{"error": {"code": "keyring_unavailable", ...}}`. Other fields in the same request are not saved.

Local providers such as Ollama don't need a key.

## Projects

A project is a local git repository registered with Docter. The `projects` table stores `id`, `name` (the directory name), `path` (absolute, resolved, unique), `current_branch`, `index_status` (`idle` | `running` | `done` | `failed`), `indexed_at`, and `created_at`. Timestamps are UTC.

| Endpoint                      | Description                                                                   |
|-------------------------------|-------------------------------------------------------------------------------|
| `POST /api/projects {path}`   | Validates and registers a repository. Returns `201` with `is_dirty` and `warnings`. |
| `GET /api/projects`           | Lists all projects, oldest first.                                             |
| `GET /api/projects/{id}`      | Returns one project.                                                          |
| `DELETE /api/projects/{id}`   | Removes the database record only (`204`). Files on disk are never touched.    |

Validation for `POST /api/projects`. Every error uses the standard error shape:

| Case                                          | Status | `code`                |
|-----------------------------------------------|--------|-----------------------|
| Relative path (`~` is expanded)               | 400    | `invalid_path`        |
| Path does not exist                           | 400    | `path_not_found`      |
| Path is not a directory                       | 400    | `not_a_directory`     |
| Not a git repository, or a bare repository    | 400    | `not_a_git_repo`      |
| Subdirectory of a repository (not its root)   | 400    | `not_repository_root` |
| Git failed to read the repository             | 400    | `git_error`           |
| Same resolved path already registered         | 409    | `project_exists`      |

The path is resolved before it is stored, so symlinks and `.` segments map to the same project. `current_branch` is `null` for a detached HEAD.

A dirty working tree, including untracked files, does not block registration. The response has `is_dirty: true` and a message in `warnings`.

## Development

```bash
uv run pytest                                      # tests
uv run ruff check . && uv run ruff format --check . # lint and format check
uv run mypy                                        # strict type checking
```

Tests are in `backend/tests/` and mirror the source layout. Shared fixtures are defined in `conftest.py`:

- `data_dir`: a temporary directory, exported as `DOCTER_DATA_DIR`.
- `settings`: a `Settings` instance bound to that directory.
- `app`: the application, built with `create_app(settings)`.
- `client`: an `httpx.AsyncClient` with the lifespan running.
- `memory_keyring` (autouse): replaces the OS keyring with `tests/fakes.py::InMemoryKeyring`, so tests never touch real credentials.
- `make_git_repo`: a factory for temporary git repositories on branch `main`, optionally with an initial commit or as a bare repository (see `tests/git_utils.py`).

LiteLLM is never called for real in tests. Tests replace `litellm.acompletion` with a fake using `monkeypatch`, and `conftest.py` sets `LITELLM_LOCAL_MODEL_COST_MAP=True` so importing LiteLLM stays offline.

### Conventions

- Use async for all I/O. Keep routers thin, put logic in services, and inject services with `Depends`.
- Keep Pydantic request and response schemas separate from SQLModel tables.
- Use full type hints (mypy strict) and add docstrings to public functions.
- Never store or log secrets. API keys belong in the OS keyring.
- Every change ships with tests. Tests, ruff, and mypy must all pass.
