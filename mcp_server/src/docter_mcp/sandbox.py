"""The only filesystem access layer for target repositories."""

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path, PurePosixPath, PureWindowsPath

from mcp.server.mcpserver.exceptions import ToolError
from pathspec import GitIgnoreSpec

from docter_contract import ErrorCode, ToolErrorInfo


def fail(code: ErrorCode, message: str) -> ToolError:
    """Build an anticipated tool failure using the shared contract."""
    return ToolError(ToolErrorInfo(code=code, message=message).to_message())


class Sandbox:
    """Validate repository roots and confine all subsequent reads to them."""

    def __init__(self, repo: str | Path, allowed_roots: tuple[Path, ...] = ()) -> None:
        candidate = Path(repo).expanduser()
        if not candidate.is_absolute():
            raise fail(ErrorCode.REPO_NOT_FOUND, "Repository path must be absolute.")
        if ".git" in (part.lower() for part in candidate.parts):
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, "Git metadata cannot be accessed.")
        try:
            self.root = candidate.resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise fail(ErrorCode.REPO_NOT_FOUND, f"Cannot resolve repository: {repo!r}") from exc
        if ".git" in (part.lower() for part in self.root.parts):
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, "Git metadata cannot be accessed.")
        if allowed_roots and not any(
            self.root.is_relative_to(root.expanduser().resolve()) for root in allowed_roots
        ):
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, "Repository is outside DOCTER_ALLOWED_ROOTS.")
        if not self.root.is_dir():
            raise fail(ErrorCode.REPO_NOT_FOUND, f"Repository does not exist: {self.root}")
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
                env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
            )
        except OSError as exc:
            raise ToolError(f"Cannot validate repository with Git: {exc}") from exc
        if result.returncode or Path(result.stdout.strip()).resolve() != self.root:
            raise fail(ErrorCode.NOT_A_GIT_REPO, "Repository must be a Git working tree root.")

    def resolve(self, path: str) -> Path:
        """Resolve a repo-relative file, rejecting traversal, metadata and escaping links."""
        relative = PurePosixPath(path)
        if (
            not path
            or "\\" in path
            or "\0" in path
            or relative.is_absolute()
            or PureWindowsPath(path).drive
            or ".." in relative.parts
            or any(part.lower() == ".git" for part in relative.parts)
        ):
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, f"Unsafe repository path: {path!r}")
        try:
            resolved = (self.root / path).resolve()
        except (OSError, RuntimeError) as exc:
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, f"Cannot resolve path: {path!r}") from exc
        if not resolved.is_relative_to(self.root) or any(
            part.lower() == ".git" for part in resolved.relative_to(self.root).parts
        ):
            raise fail(ErrorCode.PATH_OUTSIDE_REPO, f"Path escapes the repository: {path!r}")
        return resolved

    def read_text(self, path: str) -> str:
        """Read a UTF-8 file without normalizing its line endings."""
        resolved = self.resolve(path)
        try:
            if not resolved.is_file():
                raise fail(ErrorCode.FILE_NOT_FOUND, f"Not a regular file: {path}")
            with resolved.open(encoding="utf-8", newline="") as stream:
                return stream.read()
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise fail(ErrorCode.FILE_NOT_FOUND, f"File does not exist: {path}") from exc
        except (OSError, UnicodeError) as exc:
            raise ToolError(f"Cannot read UTF-8 file {path!r}: {exc}") from exc

    def is_file(self, path: str) -> bool:
        """Check existence only after enforcing the sandbox boundary."""
        return self.resolve(path).is_file()

    def is_dir(self, path: str) -> bool:
        """Check for a directory only after enforcing the sandbox boundary."""
        return self.resolve(path).is_dir()

    def exists(self, path: str) -> bool:
        """Check whether a confined path exists, including non-regular files."""
        return self.resolve(path).exists()

    def files(self, *, respect_gitignore: bool = False) -> Iterator[str]:
        """List confined files, optionally applying root and nested Git ignore rules."""

        def on_error(error: OSError) -> None:
            raise ToolError(f"Cannot enumerate repository: {error}") from error

        ignores: dict[Path, list[tuple[Path, GitIgnoreSpec]]] = {}
        for directory, dirs, names in os.walk(self.root, onerror=on_error, followlinks=False):
            base = Path(directory)
            rules = list(ignores.get(base.parent, []))
            if respect_gitignore and ".gitignore" in names:
                text = self.read_text((base / ".gitignore").relative_to(self.root).as_posix())
                rules.append((base, GitIgnoreSpec.from_lines(text.splitlines())))
            ignores[base] = rules

            dirs[:] = sorted(
                d
                for d in dirs
                if d.lower() != ".git"
                and not (base / d).is_symlink()
                and not _ignored(base / d, rules, is_dir=True)
            )
            for name in sorted(names):
                if name.lower() != ".git" and not _ignored(base / name, rules):
                    yield (base / name).relative_to(self.root).as_posix()


def _ignored(path: Path, rules: list[tuple[Path, GitIgnoreSpec]], *, is_dir: bool = False) -> bool:
    matched = False
    for scope, spec in rules:
        relative = path.relative_to(scope).as_posix() + ("/" if is_dir else "")
        result = spec.check_file(relative)
        if result.include is not None:
            matched = result.include
    return matched
