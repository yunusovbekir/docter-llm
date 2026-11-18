"""Validate proposals against disk and render previews outside the repository."""

import codecs
import os
import subprocess
from dataclasses import dataclass
from io import StringIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from tempfile import TemporaryDirectory

from unidiff import PatchSet, UnidiffParseError

from backend.config import Settings
from backend.errors import DocterError
from docter_contract import ProposePatchOutput


class InvalidPatchError(DocterError):
    """A proposal cannot safely and cleanly apply to its declared file."""

    code = "invalid_patch"


@dataclass(frozen=True)
class ValidatedPatch:
    """Validated contents and original hunk headers, without modifying the project."""

    file_path: str
    original_content: str
    proposed_content: str
    headers: list[str]


def validate_patch(repo: Path, change: ProposePatchOutput) -> ValidatedPatch:
    """Check a single-file text patch with Git and preview it in a temporary directory.

    This function performs blocking I/O; async callers must use ``asyncio.to_thread``.
    """
    try:
        return _validate_patch(repo.resolve(strict=True), change)
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        raise InvalidPatchError(f"Cannot validate patch: {exc}. Read the file and retry.") from exc


def _validate_patch(root: Path, change: ProposePatchOutput) -> ValidatedPatch:
    relative, target = _resolve_path(root, change.file_path)
    try:
        patch_set = PatchSet(StringIO(change.patch, newline="\n"))
    except UnidiffParseError as exc:
        raise InvalidPatchError(f"Malformed unified diff: {exc}. Regenerate the patch.") from exc
    if len(patch_set) != 1:
        raise InvalidPatchError("Supply a unified diff for exactly one file.")
    patched = patch_set[0]
    if patched.is_binary_file or (not patched and not patched.is_added_file):
        raise InvalidPatchError("Supply a nonempty text patch or an empty new-file patch.")
    for path, prefix in ((patched.source_file, "a/"), (patched.target_file, "b/")):
        if path == "/dev/null":
            continue
        decoded = _decode_path(path)
        candidate, _ = _resolve_path(root, decoded.removeprefix(prefix))
        if candidate != relative:
            raise InvalidPatchError("Patch paths must match file_path; renames are not supported.")
    if change.is_new_file != patched.is_added_file:
        raise InvalidPatchError("is_new_file must match the patch's creation of a new file.")
    for line in patched.patch_info or ():
        if line.startswith(("rename ", "copy ", "old mode ", "new mode ")):
            raise InvalidPatchError("Only file content changes are supported.")
        has_mode = line.startswith(("new file mode ", "deleted file mode ")) or (
            line.startswith("index ") and len(line.split()) == 3
        )
        if has_mode and line.split()[-1] not in {"100644", "100755"}:
            raise InvalidPatchError("Only regular text files may be proposed.")
    if not patched.is_added_file and not target.is_file():
        raise InvalidPatchError("The patch's source must be an existing regular text file.")

    patch_bytes = change.patch.encode("utf-8")
    # Numstat confirms Git interprets the same single target as the diff parser.
    stats = _git_apply(root, patch_bytes, "--check", "--numstat", "-z")
    records = stats.split(b"\0")
    if len(records) != 2 or records[1] or records[0].split(b"\t", 2)[-1] != relative.encode():
        raise InvalidPatchError("Git's patch target must match file_path exactly.")
    original = b"" if patched.is_added_file else target.read_bytes()
    original_content = original.decode("utf-8")
    with TemporaryDirectory(prefix="docter-preview-") as directory:
        preview_root = Path(directory)
        preview = preview_root / relative
        if not patched.is_added_file:
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_bytes(original)
        _git_apply(preview_root, patch_bytes, "--no-index")
        if not patched.is_removed_file and (preview.is_symlink() or not preview.is_file()):
            raise InvalidPatchError("The proposed result must be a regular text file.")
        proposed = b"" if patched.is_removed_file else preview.read_bytes()
    headers = [
        line.rstrip("\r\n")
        for line in StringIO(change.patch, newline="\n")
        if line.startswith("@@ ")
    ]
    if len(headers) != len(patched):
        raise InvalidPatchError("Patch hunk headers could not be parsed consistently.")
    return ValidatedPatch(relative, original_content, proposed.decode("utf-8"), headers)


def _resolve_path(root: Path, path: str) -> tuple[str, Path]:
    relative = PurePosixPath(path)
    if (
        not path
        or relative == PurePosixPath(".")
        or "\\" in path
        or "\0" in path
        or relative.is_absolute()
        or PureWindowsPath(path).drive
        or ".." in relative.parts
        or any(part.lower() == ".git" for part in relative.parts)
    ):
        raise InvalidPatchError(f"Unsafe repository-relative file path: {path!r}.")
    candidate = root / path
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or any(
        part.lower() == ".git" for part in resolved.relative_to(root).parts
    ):
        raise InvalidPatchError(f"Path escapes the repository or accesses Git metadata: {path!r}.")
    # Git refuses symlink traversal, including links whose targets stay inside the repo.
    if any(parent.is_symlink() for parent in (candidate, *candidate.parents) if parent != root):
        raise InvalidPatchError("Patch paths must not traverse symlinks.")
    return relative.as_posix(), resolved


def _decode_path(path: str) -> str:
    if path.startswith('"') and path.endswith('"'):
        return codecs.escape_decode(path[1:-1].encode("utf-8"))[0].decode("utf-8")
    return path


def _git_apply(root: Path, patch: bytes, *options: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", *options, "-"],
            cwd=root,
            input=patch,
            capture_output=True,
            check=False,
            timeout=Settings.DEFAULT_REQUEST_TIMEOUT_S,
            env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        )
    except subprocess.TimeoutExpired as exc:
        raise InvalidPatchError(
            "Git patch validation timed out. Retry with a smaller patch."
        ) from exc
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise InvalidPatchError(
            f"Patch does not apply cleanly: {detail}. Read the current file and regenerate it."
        )
    return result.stdout
