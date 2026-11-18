"""Static Markdown fences, documentation anchors, and local link discovery."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from html import unescape


@dataclass(frozen=True, slots=True)
class CodeBlock:
    """A fenced block and the 1-based line where its content starts."""

    info: str
    code: str
    line: int


def markdown_parts(text: str) -> tuple[list[str], list[CodeBlock]]:
    """Blank fenced code in prose while retaining line numbers and complete blocks."""
    lines = text.splitlines(keepends=True)
    prose = list(lines)
    fence: str | None = None
    info = ""
    start = 0
    blocks = []
    for index, line in enumerate(lines):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence is None:
            if match:
                fence, info = match.groups()
                start = index + 1
                prose[index] = "\n"
        else:
            prose[index] = "\n"
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                blocks.append(CodeBlock(info.strip(), "".join(lines[start:index]), start + 1))
                fence = None
    if fence is not None:
        blocks.append(CodeBlock(info.strip(), "".join(lines[start:]), start + 1))
    return prose, blocks


def prose_lines(path: str, text: str) -> list[str]:
    """Exclude fenced Markdown and indented reStructuredText literal/directive bodies."""
    if not path.lower().endswith(".rst"):
        return markdown_parts(text)[0]
    lines = text.splitlines(keepends=True)
    prose = list(lines)
    literal_indent: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip())
        if literal_indent is not None:
            if not stripped or indent > literal_indent:
                prose[index] = "\n"
                continue
            literal_indent = None
        if stripped.endswith("::") or re.match(
            r"\.\. (?:code|code-block|sourcecode|literalinclude)::", stripped
        ):
            literal_indent = indent
    return prose


def _slug(text: str) -> str:
    text = re.sub(r"!?\[([^]]*)]\([^)]*\)", r"\1", text)
    text = unescape(text)
    text = re.sub(r"<[^>]*>", "", text).strip().lower()
    return re.sub(r"[^\w -]", "", text).replace(" ", "-")


def anchors(path: str, text: str) -> set[str]:
    """Recognize heading slugs, duplicate Markdown headings, and explicit HTML/RST IDs."""
    lines = prose_lines(path, text)
    result: set[str] = set()
    counts: dict[str, int] = {}
    rst = path.lower().endswith(".rst")
    for index, line in enumerate(lines):
        result.update(re.findall(r"""\b(?:id|name)\s*=\s*["']([^"']+)["']""", line))
        label = re.match(r"^\s*\.\. _([^:]+):\s*$", line) if rst else None
        if label:
            result.add(label[1])
        explicit = re.search(r"\{#([^}]+)}", line)
        if explicit:
            result.add(explicit[1])
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", line) if not rst else None
        title = heading[1] if heading else None
        if title is None and index + 1 < len(lines) and line.strip():
            underline = lines[index + 1].strip()
            if (
                underline
                and len(set(underline)) == 1
                and underline[0] in ("=-~^\"'`:+*#" if rst else "=-")
                and len(underline) >= len(line.strip())
            ):
                title = line.strip()
        if title is not None and explicit is None:
            slug = _slug(re.sub(r"\s*\{#[^}]+}", "", title))
            count = counts.get(slug, 0)
            candidate = f"{slug}-{count}" if count and not rst else slug
            while candidate in result and not rst:
                count += 1
                candidate = f"{slug}-{count}"
            result.add(candidate)
            counts[slug] = count + 1
    return result


def links(path: str, text: str) -> Iterator[tuple[int, str]]:
    """Yield inline and reference-style Markdown/RST link destinations, not external roles."""
    lines = prose_lines(path, text)
    if path.lower().endswith(".rst"):
        for number, line in enumerate(lines, 1):
            for match in re.finditer(r"`[^`<]*<([^>]+)>`_+", line):
                yield number, match[1].strip()
            rst_target = re.match(r"^\s*\.\. _[^:]+:\s+(\S+)\s*$", line)
            if rst_target:
                yield number, rst_target[1]
        return
    definitions: dict[str, str] = {}
    for number, line in enumerate(lines, 1):
        definition = re.match(r"^ {0,3}\[([^]]+)]:\s*(?:<([^>]+)>|(\S+))", line)
        if definition:
            target = definition[2] or definition[3]
            definitions[" ".join(definition[1].lower().split())] = target
            yield number, target
    for number, line in enumerate(lines, 1):
        if re.match(r"^ {0,3}\[[^]]+]:", line):
            continue
        line = re.sub(r"(`+).*?\1", "", line)
        inline = r"!?\[[^]]*]\(\s*(?:<([^>]+)>|((?:[^()\s]|\([^()]*\))+))"
        for match in re.finditer(inline, line):
            yield number, match[1] or match[2]
        for match in re.finditer(r"!?\[([^]]+)]\[([^]]*)]", line):
            label = " ".join((match[2] or match[1]).lower().split())
            if label not in definitions:
                yield number, f"[{label}]"
