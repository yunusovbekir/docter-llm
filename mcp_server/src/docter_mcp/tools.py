"""Read-only foundation tools; inputs and outputs come from docter_contract."""

import re
from importlib.metadata import version
from pathlib import PurePosixPath

from docter_contract import (
    CONTRACT_VERSION,
    RESOURCE_URIS,
    DocFile,
    DocFormat,
    DocFramework,
    GetServerInfoInput,
    GetServerInfoOutput,
    ListDocFilesInput,
    ListDocFilesOutput,
    ReadFileInput,
    ReadFileOutput,
    ServerCapabilities,
)
from docter_mcp.config import Config
from docter_mcp.sandbox import Sandbox


def get_server_info(request: GetServerInfoInput, config: Config) -> GetServerInfoOutput:
    """Describe this server's versions and currently implemented capabilities."""
    return GetServerInfoOutput(
        contract_version=CONTRACT_VERSION,
        server_version=version("docter-mcp"),
        capabilities=ServerCapabilities(
            tools=list(config.TOOL_NAMES),
            resources=list(RESOURCE_URIS),
            execution_enabled=config.allow_exec,
        ),
    )


def read_file(request: ReadFileInput, config: Config) -> ReadFileOutput:
    """Read a whole file or an inclusive 1-based line range."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    text = sandbox.read_text(request.file_path)
    lines = text.splitlines(keepends=True)
    start = (request.start_line or 1) - 1
    end = request.end_line if request.end_line is not None else len(lines)
    return ReadFileOutput(
        file_path=request.file_path,
        content="".join(lines[start:end]),
        total_lines=len(lines),
    )


def list_doc_files(request: ListDocFilesInput, config: Config) -> ListDocFilesOutput:
    """Find Markdown/reStructuredText files and detect MkDocs or Sphinx without execution."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    paths = sorted(sandbox.files())
    if sandbox.is_file("mkdocs.yml") or sandbox.is_file("mkdocs.yaml"):
        framework = DocFramework.MKDOCS
    elif any(PurePosixPath(path).name == "conf.py" for path in paths):
        framework = DocFramework.SPHINX
    else:
        framework = DocFramework.NONE
    files = []
    for path in paths:
        suffix = PurePosixPath(path).suffix.lower()
        if suffix not in {".md", ".rst"}:
            continue
        text = sandbox.read_text(path)
        format_ = DocFormat.MARKDOWN if suffix == ".md" else DocFormat.RST
        files.append(DocFile(path=path, format=format_, title=_title(text, format_)))
    return ListDocFilesOutput(framework=framework, files=files)


def _title(text: str, format_: DocFormat) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if format_ is DocFormat.MARKDOWN:
            match = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
            if match:
                return match.group(1)
        if index + 1 < len(lines) and line.strip():
            underline = lines[index + 1].strip()
            markers = "=-" if format_ is DocFormat.MARKDOWN else "=-~^\"'`:+*#"
            if (
                underline
                and len(set(underline)) == 1
                and underline[0] in markers
                and len(underline) >= len(line.strip())
            ):
                return line.strip()
    return None
