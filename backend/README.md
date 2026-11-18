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

Fixed defaults, timeouts, logging constants, and shared validation helpers also live on
the `Settings` class in `config.py` as `ClassVar` attributes. These are not environment
fields. Define configuration on this class, not as module-level variables in consumers.

| Variable                     | Default                        | Description                                         |
|------------------------------|--------------------------------|-----------------------------------------------------|
| `DOCTER_DATA_DIR`          | `~/.docter`                  | Root directory for all local data                   |
| `DOCTER_HOST`              | `127.0.0.1`                    | Bind address                                        |
| `DOCTER_PORT`              | `8000`                         | Bind port                                           |
| `DOCTER_LOG_LEVEL`         | `INFO`                         | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`  |
| `DOCTER_CORS_ORIGINS`      | `["http://localhost:5173"]`    | Allowed CORS origins, given as a JSON list          |
| `DOCTER_FRONTEND_DIST_DIR` | `<repo>/frontend/dist`         | Directory containing the built Vue app              |
| `DOCTER_MCP_COMMAND`       | `uv run docter-mcp`            | Command that starts the Docter MCP server (shell-style quoting) |

### Data directory layout

These paths are derived from `data_dir`. They are created at startup.

```
~/.docter/
  docter.db     # SQLite database
  index/          # vector indexes
  worktrees/      # git worktrees for sessions
  logs/           # docter.log (rotating, 5 MB x 3) and mcp-server.log (MCP server stderr)
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
| `mcp_client/`       | `MCPManager` (`manager.py`): runs the Docter MCP server over stdio and calls its tools |
| `llm/`             | LiteLLM gateway, typed completion events, MCP tool conversion, and sampling callback |
| `agent/`           | [Mode policies, cancellable orchestration, typed events, and proposal sinks](agent/README.md) |

### Lifespan

At startup the app creates the data directories, configures logging, creates the async engine, runs `create_all`, and stores `engine`, `sessionmaker`, an `MCPManager` (`mcp`), and an `IndexingService` (`indexing`) on `app.state`. At shutdown it cancels and awaits indexing jobs, stops the MCP server, if running, and disposes of the engine.

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
| `POST /api/projects/{id}/index` | Starts a background MCP `index_repo {path}` job (`202`); returns `409` (`index_running`) if one is already running for this project. |
| `GET /api/projects/{id}/index/status` | Returns `{status, progress, message, indexed_at, error}`. Unknown projects return `404`. |

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

### Indexing jobs

The app-wide `IndexingService` owns one asyncio task per active project. Jobs use their
own database sessions, not request sessions. MCP progress notifications with a positive
total are converted to percentages and clamped to 0-100; notifications without a total
update the message but leave the percentage unchanged. Success sets `index_status`
to `done`, progress to 100, and `indexed_at` to the current UTC time. Tool errors,
transport failures, and timeouts set the status to `failed` and expose an error message.
The indexing request timeout is `Settings.INDEX_TIMEOUT_S` (one hour).

Progress, messages, and errors are in memory and reset on application restart; project
status and the last successful `indexed_at` are persisted. A failed re-index preserves
the previous successful timestamp. Completed or failed jobs can be started again.
Graceful shutdown cancels active jobs and marks them failed before closing the database.
The registry is process-local: run a single backend worker.

## MCP client

`MCPManager` (`mcp_client/manager.py`) runs the Docter MCP server as a subprocess, started with `DOCTER_MCP_COMMAND`, and talks to it over stdio with the official `mcp` SDK. Inject it with `MCPManagerDep` from `api/deps.py`.

- **Lifecycle:** one `ClientSession`, opened on first use and kept open by a background task that holds the transport in an `AsyncExitStack`. It is closed at app shutdown. The server's stderr is appended to `logs/mcp-server.log`.
- **`list_tools()`:** returns the server's tools. The result is cached until the session is closed or restarted.
- **`call_tool(name, args, timeout, progress_callback=None)`:** returns a `ToolResult` with `text`, `structured`, and `is_error`. A tool that runs but fails returns `is_error: true` instead of raising. `progress_callback(progress, total, message)` receives the server's progress notifications.
- **`restart()`:** stops the server and starts a new one.
- **Crash recovery:** if the connection is lost, the failing call raises `MCPError`, and the next call starts a new server.
- **Sampling:** the constructor accepts an optional `sampling_callback` for `sampling/createMessage` requests from the server. The application supplies `llm/sampling.py::SamplingHandler`, which reloads saved settings and the provider's keyring key for each request.

Failures are raised as domain errors:

| Case                                                 | Status | `code`        |
|------------------------------------------------------|--------|---------------|
| The server failed to start, the connection was lost, or a request failed | 502 | `mcp_error` |
| The server did not start, or a tool did not finish, within the timeout | 504 | `mcp_timeout` |

## LLM gateway

`SettingsService.gateway()` builds an `LLMGateway` from the saved `AppSettings`
(or defaults) and the provider's keyring key. Direct callers can also construct
`LLMGateway(settings, api_key)`. A gateway snapshots its configuration.

- `stream_chat(messages, tools=())` uses `litellm.acompletion(stream=True)` and yields
  `TextDelta(text)`, complete `ToolCallRequest(id, name, arguments)` events, `Usage`,
  then `Done`. Tool fragments are accumulated by index, and arguments must decode
  to JSON objects. Usage-only trailing chunks are consumed before completion.
- `complete(messages)` returns `Completion(text, model, stop_reason, usage)`.
  Optional `max_tokens`, `temperature`, and `stop` parameters support MCP sampling.
- Token counts and estimated `cost_usd` are `None` when unavailable, with a warning;
  an unknown model price is never reported as a free completion.
- Rate limits and timeouts get two retries, after 1 and 2 seconds. LiteLLM's internal
  retries are disabled. A stream is not retried once text has been emitted, avoiding
  duplicated output. Failed or truncated streams never emit `Done`.
- Provider failures raise `LLMError` (`502`, `llm_error`) without exposing provider
  error messages that could contain credentials.

Sampling supports text messages, the system prompt, token limit, temperature, and
stop sequences. It returns an MCP assistant message with the actual response model
and normalized stop reason. Non-text content and sampling tools are explicitly
rejected with protocol errors. Server model preferences do not override the user's
configured provider/model, and `includeContext` does not add repository or other
server context.

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
- `clean_log_handlers`: removes the log handlers a lifespan installs. Use it when a test runs the lifespan itself.
- `memory_keyring` (autouse): replaces the OS keyring with `tests/fakes.py::InMemoryKeyring`, so tests never touch real credentials.
- `make_git_repo`: a factory for temporary git repositories on branch `main`, optionally with an initial commit or as a bare repository (see `tests/git_utils.py`).
- `stub_mcp_command`: runs `tests/fixtures/stub_mcp_server.py`, an `MCPServer` (formerly FastMCP) with the tools `echo`, `index_repo` (reports progress), and `propose_patch` (returns its input as structured output).

LiteLLM is never called for real in tests. Tests replace `litellm.acompletion` with a fake using `monkeypatch`, and `conftest.py` sets `LITELLM_LOCAL_MODEL_COST_MAP=True` so importing LiteLLM stays offline.

### Conventions

- Use async for all I/O. Keep routers thin, put logic in services, and inject services with `Depends`.
- Keep Pydantic request and response schemas separate from SQLModel tables.
- Use full type hints (mypy strict) and add docstrings to public functions.
- Never store or log secrets. API keys belong in the OS keyring.
- Every change ships with tests. Tests, ruff, and mypy must all pass.
