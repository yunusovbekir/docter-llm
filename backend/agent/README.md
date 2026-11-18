# Agent

`Orchestrator` in `orchestrator.py` combines a streaming `LLMGateway`, an
`MCPManager`, and a project/session-scoped `ChangeSink`. It does not own the
shared MCP manager's lifetime, expose HTTP endpoints, or apply patches.

## Orchestration

```python
from contextlib import aclosing

from backend.agent import InMemoryChangeSink, Mode, Orchestrator

sink = InMemoryChangeSink()
orchestrator = Orchestrator(gateway, mcp, sink, max_agent_steps=25)
async with aclosing(orchestrator.run(history, user_message, Mode.ADD, {}, project)) as run:
    async for event in run:
        print(event.model_dump_json())
```

Pass the saved `AppSettings.max_agent_steps` to the constructor (1-200; default
25). Each step is one LLM completion, not one tool invocation. The run validates
mode parameters, loads its system prompt, adds project/mode context, copies the
conversation history, and appends the new user message.

Only the intersection of the mode allowlist and the server's available tools is
advertised or executed. Repository tool arguments are bound to `project.path`,
even if the model supplies a different `repo`. Tool calls execute sequentially
only after the LLM stream completes successfully. The next completion receives
the assistant's text and complete tool calls, followed by one correlated text
response for each call. MCP failures, tool-reported errors, blocked/unavailable
tools, invalid patch or translation results, and sink rejections become
tool-error text so the model can retry.

`AgentEvent` is a discriminated Pydantic union with a stable `type` field:

| Type | Payload |
|------|---------|
| `token` | `text` |
| `tool_call` | `id`, `name`, project-bound `arguments` |
| `tool_result` | `id`, `name`, `text`, `structured`, `is_error` |
| `change_proposed` | `change` (validated `ProposePatchOutput`) |
| `warning` | `code`, `message` |
| `error` | `code`, `message` |
| `done` | `reason` (`completed`, `max_steps`, or `error`), attempted `steps` |

A no-tool completion ends normally. If the last permitted completion requests
tools, those calls are processed, then a `max_agent_steps` warning and `done`
are emitted without another LLM request. A terminal domain error emits `error`,
then `done`; it does not execute calls from an incomplete LLM response.

Cancellation propagates as `asyncio.CancelledError`, closes the active LLM
stream, and does not emit a misleading terminal event or close the shared MCP
manager. Consumers stopping iteration early must close the iterator, as above.
Already accepted proposals remain in the sink.

## Change sinks

`ChangeSink.add(change)` is async and returns `Ok()` or `Error(message)` from
`changes.py`. The sink is responsible for validation/storage appropriate to its
project/session scope; a rejection message should tell the model how to repair
the proposal. A `change_proposed` event is emitted only after `Ok`. An `Error`
becomes the corresponding tool response, not a successful proposal event.

`InMemoryChangeSink` implements the protocol and exposes accepted proposals in
`changes` for tests. Patch results use the shared contract's
`ProposePatchOutput`, including `is_new_file`; structured MCP output and legacy
JSON text output are supported. Nothing is written to the project.

`translate_file` results are validated as `TranslateFileOutput` in either response
format, then adapted to `ProposePatchOutput` using `target_path` as `file_path`
(not `source_path`). The patch, rationale, and `is_new_file` flag are preserved
for both new and existing translation targets. Each translation warning emits a
`warning` event with code `translation_warning` after the tool result and before
any `change_proposed` event. Validated warnings remain visible if the sink rejects
the proposal, but malformed output and tool failures emit no translation warnings
or successful proposals. Translation proposals use the same sink acceptance
workflow as `propose_patch`; neither source nor target documentation is modified.

## Modes

`Mode` in `modes.py` defines the mode policy.

| Mode | Additional allowed tools | Required parameters |
|------|--------------------------|---------------------|
| `add` | `find_doc_gaps`, `propose_patch` | None |
| `fix` | `check_doc_drift`, `run_doc_examples`, `propose_patch` | None |
| `find_missing` | `find_doc_gaps` | None |
| `translate` | `translate_file`, `propose_patch` | `target_language` |

Every mode also allows `get_server_info`, `search_code`, `get_symbol`, `read_file`,
and `list_doc_files`. `allowed_tools` and `required_params` return fresh lists;
modifying them does not change mode policy. Unlisted tools are denied by default.
Callers must use the allowlist both when advertising tools and before executing
tool calls. `find_missing` excludes patch proposals, translation, indexing, and
example execution to keep the audit read-only.

Call `mode.validate_params(params)` before starting a task. Translation requires
`target_language` to match the shared contract's `LanguageTag` type (BCP 47), for
example `ja`, `pt-BR`, or `zh-Hans`. Validation happens before LLM calls or MCP
tool discovery. Language names such as `Japanese`, surrounding whitespace,
missing values, and non-string values are rejected without inferring a tag or
normalizing the input. A missing or invalid value raises `InvalidModeParamsError`
(`422`, `invalid_mode_params`). Other modes do not validate `target_language`.

`await mode.load_system_prompt()` reads `prompts/<mode>.md` asynchronously without
depending on the current working directory. Prompts are included in the backend
wheel. A missing, unreadable, invalid UTF-8, or empty prompt raises `ModePromptError`
(`500`, `mode_prompt_error`) rather than allowing a task to run without instructions.

## Checks

Run from the repository root:

```bash
uv run pytest backend/tests/agent backend/tests/mcp_client
uv run ruff check .
uv run mypy
```
