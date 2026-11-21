# Docter `beta`

Docter is an AI agent that runs on your own machine and works on the documentation of open-source libraries and SDKs. Point it at a local repository and it can:

- **Find missing docs:** produce a coverage report of public APIs that have no documentation.
- **Add docs:** write docstrings and API reference pages.
- **Fix docs:** find docstrings that no longer match the code, broken links, and examples that fail to run.
- **Translate docs:** translate files into another language while keeping code blocks and Markdown structure intact.

## How it works

1. Select a local git repository and choose a mode.
2. Chat with the agent to give it direction and context.
3. Review each proposed change as a diff, then approve it, deny it, or comment on it. The agent revises the change based on your comments.
4. Docter commits approved changes to a dedicated branch. Your working tree is never modified directly.

Docter works with OpenAI, Anthropic, Ollama, and any other provider supported by [LiteLLM](https://github.com/BerriAI/litellm). API keys are stored in your OS keyring, never on disk.

## Quick start

On Linux or WSL, install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js
20.19+ (20.x), 22.13+ (22.x), or 24+, and pnpm 10. From the repository root, run:

```bash
bash scripts/dev.sh
```

Open [http://localhost:5173](http://localhost:5173). The launcher installs frontend
dependencies and starts the Vite frontend and FastAPI backend. The backend
starts MCP on demand and initializes its local SQLite database. Press Ctrl+C to
stop both services. If your configured LLM provider is a separate local service
such as Ollama, it still needs to be running.

Set your LLM provider and API key under **Settings**, and add a project.
Choose **Other** to enter a LiteLLM provider ID not listed in the menu. Leave
the API key field blank to keep the provider's saved key, or choose **Remove key**
and then **Save** to delete it. Select **Test connection** to check the saved
configuration and see the latency or error. Ollama can be tested without a saved key.

## Backend development

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then run from the repository root:

```bash
uv sync
uv run docter                              # http://127.0.0.1:8000
uv run pytest backend/tests                # backend tests (offline LLM/MCP fakes)
uv run ruff check backend
uv run mypy backend
```

The API docs are at http://127.0.0.1:8000/docs. Run a single backend worker: chat and
indexing jobs are process-local. Startup resets interrupted chat runs to `idle` and
interrupted indexing jobs to `failed`, allowing them to be retried.

Export the OpenAPI contract for the frontend after API changes:

```bash
uv run python -m backend.scripts.export_openapi
uv run python -m backend.scripts.export_openapi --check
```

The script writes `frontend/src/api/openapi.json` without starting the backend, MCP,
or database. The offline approval-flow test is
`uv run pytest backend/tests/test_e2e.py`; it indexes through a stub MCP server,
chats with a fake LLM, approves its proposed patch, and checks the real commit on
the session's `docsmith/` branch.

## Frontend development

Use Node.js 20.19+ (20.x), 22.13+ (22.x), or 24+, and pnpm 10. Run from the
repository root:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm gen:api
pnpm lint
pnpm typecheck
pnpm test
pnpm build   # writes frontend/dist
pnpm dev     # http://localhost:5173
```

`pnpm gen:api` regenerates `frontend/src/api/schema.d.ts` from
`http://127.0.0.1:8000/openapi.json`, or from the checked-in
`frontend/src/api/openapi.json` when the endpoint is unavailable.

Shared UI primitives live in `frontend/src/components/common/`. Bind `AppInput`
and `AppSelect` with `v-model` and provide a visible `label`. Control `AppModal`
with `:open` and set it to `false` in the `@close` handler. Set the Tailwind
`dark` class on `<html>` so it also applies to the dialog teleported to `<body>`.

Use `useToast()` for success, error, info, and warning notifications. Up to four toasts
stack at once; they dismiss after five seconds, or sooner if dismissed manually
or replaced by a newer toast. Uncaught Vue errors and unhandled promise rejections
show the API message for an `ApiError` and a generic message otherwise.

For POST-based event streams, `useEventStream(url, body)` exposes `start()`,
`abort()`, `isStreaming`, and `onEvent(handler)`. The handler receives typed
events, and `onEvent` returns an unsubscribe function. A stream never
retries automatically, including when the tab is hidden, and is aborted when
its component unmounts.

To run the services separately, start `uv run docter` from the repository root.
Vite forwards `/api` to `http://127.0.0.1:8000` by default, or to the port set by
`DOCTER_PORT`. Run `pnpm preview` from `frontend/` to serve the production build
locally.

The Docter app opens on Projects. Register a local Git repository using its
absolute path on the machine running the backend. A dirty working tree does not
block registration. Removing a project from Docter does not delete repository
files. Open a project and select Index or Re-index to build its code index. The
project page shows live progress, the last successful indexing time, and any
failure details. Once indexing is complete, start an Add, Fix, Find Missing, or
Translate session from the project page. Translate requires a target language
tag such as `de` or `pt-BR`. Open a session to see its conversation history and
stream new assistant replies. Press Enter to send or Shift+Enter for a new line.
While a run is active, the message input is disabled. Select Stop to cancel it.
Expand a tool-call card to inspect its arguments and result. Warnings and errors
appear inline in the conversation.
The Changes panel lists proposed files by change set, newest first, and refreshes
as new proposals arrive. Select a file to open its read-only diff. Switch between
side-by-side and inline layouts; the browser remembers your preference.
Approve or deny the selected change above its diff, or use **Approve all pending**
to review remaining changes in bulk after confirming. Conflicts are flagged when
a patch could not be applied.
For finer review, select pending hunks and approve or deny just those hunks.
Select a hunk header to jump to it in the diff.
Leave a review comment on the whole change or a specific hunk to request a
revision. The run streams into chat, and version links let you compare the new
proposal with its predecessor.
Find Missing results show documentation coverage and a list of gaps you can filter
by missing type or sort by file. Select symbols and choose **Send to Add mode** to
open an Add session for the same project with an editable draft. Nothing is sent
until you submit the draft.
In **History**, filter sessions by project, check how many proposals are pending,
applied, or denied, copy a session's branch name, or resume the session. Deleting
a session removes its history and worktree but keeps its Git branch, if one exists.

If you serve `frontend/dist` outside Vite, configure your server to serve `index.html`
for client-side routes, without rewriting `/api` requests.

## Documentation

- [Backend](backend/README.md): configuration, API reference, and development guide.
- [MCP server](mcp_server/README.md): stdio setup, sandbox rules, tools, and resources.
- [Tool contract](contract/README.md): the shared MCP tool schemas and how to regenerate `contract/schema.json`.
