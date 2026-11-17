# Docter MCP server

The foundation exposes `get_server_info`, `read_file`, and `list_doc_files` over stdio.
It uses the official MCP 2 SDK's `MCPServer` (the renamed FastMCP API). All tool input
and output schemas are the shared `docter_contract` models, including flat tool arguments.
The server never writes to the target repository or executes its code.

From the workspace root:

```sh
uv sync
uv run docter-mcp --repo /absolute/path/to/repo
# Equivalent:
uv run python -m docter_mcp --repo /absolute/path/to/repo
```

Only MCP messages are written to stdout; logging goes to stderr. `--repo` binds resource
reads to that repository for the lifetime of the server; it defaults to the startup working
directory. Tool calls specify their own absolute `repo` and do not change resource binding.
Repositories must be Git working-tree roots, not arbitrary subdirectories.

## Configuration

| Environment variable | Default | Meaning |
|---|---|---|
| `DOCTER_INDEX_DIR` | `~/.docter/index` | Reserved index storage location; M2 does not create it. |
| `DOCTER_ALLOWED_ROOTS` | unset | Optional comma-separated absolute paths; repos must resolve within one. |
| `DOCTER_ALLOW_EXEC` | `false` | Execution permission (`true`/`false` or `1`/`0`); M2 has no execution tool and advertises execution as disabled. |

File arguments use repository-relative `/` paths. Absolute paths, `..`, `.git` components,
and symlinks resolving outside the repo or into Git metadata are rejected. Directory
symlinks are not followed during documentation discovery. Missing files and rejected
paths produce contract-coded tool errors; unreadable or non-UTF-8 files produce explicit
read errors.

`read_file` accepts optional inclusive, 1-based `start_line` and `end_line`, preserves
line endings, and returns the total file line count. Ranges beyond EOF are truncated;
a start beyond EOF returns empty content.

`list_doc_files` returns sorted `.md`/`.rst` paths with heading-derived titles where
available. It detects root `mkdocs.yml`/`mkdocs.yaml` first, then a `conf.py` anywhere
in the repository for Sphinx, otherwise `none`. It never executes configuration files.

## Resources

- `docter://style-guide` reads `.docter/style.md`, or a built-in guide if absent.
  An unreadable or malformed-encoding file is an error, not a reason to use the default.
- `docter://glossary/{lang}` reads `.docter/glossary.yaml`, selects the requested
  BCP 47 language, and returns its term mapping as YAML. Missing files/languages and
  malformed mappings are errors. YAML is loaded safely, without constructing objects.

Example `.docter/glossary.yaml`:

```yaml
de:
  client: Kunde
fr:
  client: client
```

## Checks

```sh
uv run pytest mcp_server/tests/test_foundation.py
uv run pytest mcp_server/tests
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

Integration tests use the MCP SDK's in-memory client session; a separate stdio test checks
the installed console script. Tests copy `tests/fixtures/sample_repo` to a temporary directory
and run `git init` there, leaving the shared fixture and target repositories unchanged.
