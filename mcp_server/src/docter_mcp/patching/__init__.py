"""In-memory patch proposals; target repositories are never modified."""

from difflib import get_close_matches, unified_diff
from io import StringIO

from docter_contract import ErrorCode, ProposePatchInput, ProposePatchOutput
from docter_mcp.config import Config
from docter_mcp.sandbox import Sandbox, fail


def propose_patch(request: ProposePatchInput, config: Config) -> ProposePatchOutput:
    """Propose sequential exact replacements or full content as a Git-compatible diff."""
    sandbox = Sandbox(request.repo, config.allowed_roots)
    path = sandbox.resolve(request.file_path).relative_to(sandbox.root).as_posix()
    is_new_file = not sandbox.exists(path)
    before = "" if is_new_file and request.new_content is not None else sandbox.read_text(path)
    after = before
    if request.new_content is not None:
        after = request.new_content
    elif request.edits is not None:
        for number, edit in enumerate(request.edits, start=1):
            position = after.find(edit.old_str)
            if position == -1:
                closest = get_close_matches(
                    edit.old_str.split("\n", 1)[0], after.split("\n"), n=1, cutoff=0.0
                )
                hint = _context(after, after.find(closest[0]))
                raise fail(
                    ErrorCode.EDIT_NOT_FOUND,
                    f"{path}, edit {number}: {edit.old_str[:80]!r} was not found. "
                    f"Closest context: {hint}. Read the file and retry with exact text.",
                )
            second = after.find(edit.old_str, position + 1)
            if second != -1:
                raise fail(
                    ErrorCode.EDIT_AMBIGUOUS,
                    f"{path}, edit {number}: {edit.old_str[:80]!r} matches more than once. "
                    f"Contexts: {_context(after, position)}; {_context(after, second)}. "
                    "Include more surrounding text to select one occurrence.",
                )
            after = after[:position] + edit.new_str + after[position + len(edit.old_str) :]
    return ProposePatchOutput(
        file_path=path,
        patch=_diff(path, before, after, is_new_file=is_new_file),
        rationale=request.rationale,
        is_new_file=is_new_file,
    )


def _context(text: str, position: int) -> str:
    line = text.count("\n", 0, position) + 1
    return f"line {line}: {text[max(0, position - 40) : position + 80]!r}"


def _git_path(path: str) -> str:
    """Quote unusual UTF-8 paths using Git's C-style byte escapes."""
    if all(33 <= ord(char) < 127 and char not in {'"', "\\"} for char in path):
        return path
    return (
        '"'
        + "".join(
            chr(byte) if 32 <= byte < 127 and byte not in {34, 92} else f"\\{byte:03o}"
            for byte in path.encode("utf-8")
        )
        + '"'
    )


def _diff(path: str, before: str, after: str, *, is_new_file: bool) -> str:
    if before == after and not is_new_file:
        return ""
    old_path, new_path = _git_path(f"a/{path}"), _git_path(f"b/{path}")
    parts = [f"diff --git {old_path} {new_path}\n"]
    if is_new_file:
        parts.append("new file mode 100644\n")
    # Git counts LF-delimited lines, not all the separators recognized by str.splitlines.
    for line in unified_diff(
        list(StringIO(before, newline="\n")),
        list(StringIO(after, newline="\n")),
        fromfile="/dev/null" if is_new_file else old_path,
        tofile=new_path,
    ):
        parts.append(line)
        if not line.endswith("\n"):
            parts.append("\n\\ No newline at end of file\n")
    return "".join(parts)
