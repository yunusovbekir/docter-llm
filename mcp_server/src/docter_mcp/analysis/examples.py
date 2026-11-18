"""Explicitly opt-in execution of documentation examples in isolated subprocesses."""

import ast
import doctest
import json
import logging
import os
import signal
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import ClassVar

from mcp.server.mcpserver.exceptions import ToolError

from docter_contract import (
    ErrorCode,
    ExampleResult,
    RunDocExamplesInput,
    RunDocExamplesOutput,
)
from docter_mcp.analysis.markup import markdown_parts
from docter_mcp.config import Config
from docter_mcp.parsing.python import module_name
from docter_mcp.sandbox import Sandbox, fail


@dataclass(frozen=True, slots=True)
class _Example:
    line: int
    code: str
    module: str | None = None


class _Runner:
    # The child receives only JSON over stdin; repository text is never interpolated into code.
    SCRIPT: ClassVar[str] = """
import doctest
import importlib
import json
import sys

example = json.load(sys.stdin)
if example["module"] is not None:
    namespace = vars(importlib.import_module(example["module"])).copy()
    test = doctest.DocTestParser().get_doctest(
        example["code"], namespace, example["module"], example["file"], example["line"] - 1
    )
    result = doctest.DocTestRunner(verbose=True).run(test)
    sys.exit(1 if result.failed else 0)
else:
    code = "\\n" * (example["line"] - 1) + example["code"]
    exec(compile(code, example["file"], "exec"), {"__name__": "__main__"})
"""


def _examples(path: str, text: str) -> list[_Example]:
    if PurePosixPath(path).suffix.lower() == ".md":
        return [
            _Example(block.line, block.code)
            for block in markdown_parts(text)[1]
            if _tokens(block.info) >= {"python", "runnable"}
        ]
    if PurePosixPath(path).suffix.lower() != ".py":
        return []
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError, RecursionError) as exc:
        logging.getLogger(__name__).warning("%s: Skipped: invalid Python syntax (%s)", path, exc)
        return []
    examples = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        statement = node.body[0]
        assert isinstance(statement, ast.Expr)
        try:
            prompts = doctest.DocTestParser().get_examples(doc, path)
        except ValueError as exc:
            raise ToolError(f"Cannot parse doctest in {path}:{statement.lineno}: {exc}") from exc
        if prompts:
            examples.append(
                _Example(
                    statement.lineno + prompts[0].lineno,
                    "".join(doc.splitlines(keepends=True)[prompts[0].lineno :]),
                    module_name(path),
                )
            )
    return sorted(examples, key=lambda example: example.line)


def _tokens(info: str) -> set[str]:
    """Accept both ``python runnable`` and attribute-style ``{.python .runnable}`` fences."""
    return {token.strip("{}.").lower() for token in info.split()}


def run_doc_examples(request: RunDocExamplesInput, config: Config) -> RunDocExamplesOutput:
    """Run trusted repo code only after opt-in; a subprocess is not a security sandbox."""
    if not config.allow_exec:
        raise fail(
            ErrorCode.EXECUTION_DISABLED,
            "Set DOCSMITH_ALLOW_EXEC=true only for trusted repositories; examples execute "
            "arbitrary code with the server's permissions.",
        )
    sandbox = Sandbox(request.repo, config.allowed_roots)
    if request.file_path is not None:
        sources = {request.file_path: sandbox.read_text(request.file_path)}
    else:
        sources = {
            path: sandbox.read_text(path)
            for path in sorted(sandbox.files(respect_gitignore=True))
            if PurePosixPath(path).suffix.lower() in {".py", ".md"}
        }
    # Validate all sources and examples before starting any repository code.
    examples = [
        (path, example) for path, text in sources.items() for example in _examples(path, text)
    ]
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(sandbox.root), str(sandbox.resolve("src")), env.get("PYTHONPATH", "")]
    )
    results = []
    for path, example in examples:
        try:
            with subprocess.Popen(
                [sys.executable, "-B", "-c", _Runner.SCRIPT],
                cwd=sandbox.root,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=os.name == "posix",
            ) as process:
                try:
                    stdout, stderr = process.communicate(
                        json.dumps(
                            {
                                "file": path,
                                "line": example.line,
                                "code": example.code,
                                "module": example.module,
                            }
                        ),
                        timeout=config.EXAMPLE_TIMEOUT,
                    )
                except subprocess.TimeoutExpired:
                    # Descendants may inherit the pipes, so kill the whole POSIX group.
                    if os.name == "posix":
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    stdout, stderr = process.communicate()
                    passed = False
                    output = (
                        stdout + stderr + f"\nExample timed out after {config.EXAMPLE_TIMEOUT}s."
                    )
                else:
                    passed = process.returncode == 0
                    output = stdout + stderr
                    if not passed and not output:
                        output = f"Example exited with status {process.returncode}."
        except OSError as exc:
            raise ToolError(
                f"Cannot start example subprocess for {path}:{example.line}: {exc}"
            ) from exc
        results.append(ExampleResult(file=path, line=example.line, passed=passed, output=output))
    return RunDocExamplesOutput(results=results)
