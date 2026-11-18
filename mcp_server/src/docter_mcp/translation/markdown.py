"""Lossless Markdown segmentation and protection of non-translatable syntax."""

import re
from collections import Counter
from dataclasses import dataclass
from uuid import uuid4

import yaml
from mcp.server.mcpserver.exceptions import ToolError
from yaml.nodes import MappingNode, Node, SequenceNode


@dataclass(frozen=True)
class ProtectedMarkdown:
    """A document whose literal syntax has collision-free, individually checked tokens."""

    text: str
    placeholders: dict[str, str]
    prefix: str

    def restore(self, source: str, translated: str) -> str:
        """Reject missing, duplicated, or invented tokens before restoring literals."""
        pattern = re.escape(self.prefix) + r"\d+__"
        expected = Counter(re.findall(pattern, source))
        actual = Counter(re.findall(pattern, translated))
        if actual != expected:
            raise ValueError("Missing, duplicated, or unexpected placeholders.")
        restored = re.sub(pattern, lambda match: self.placeholders[match[0]], translated)
        if self.prefix in restored:
            raise ValueError("An unrestored or malformed placeholder remains.")
        return restored


def protect(text: str) -> ProtectedMarkdown:
    """Replace code, destinations, URLs, tags, and YAML keys without changing literals."""
    prefix = f"__DOCTER_{uuid4().hex}_"
    while prefix in text:
        prefix = f"__DOCTER_{uuid4().hex}_"
    spans: list[tuple[int, int]] = []
    lines = text.splitlines(keepends=True)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    frontmatter_end = -1
    if lines and lines[0].strip("\r\n\ufeff") == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is not None:
            frontmatter_end = end
            spans.extend(
                [
                    (0, len(lines[0].rstrip("\r\n"))),
                    (offsets[end], offsets[end] + len(lines[end].rstrip("\r\n"))),
                ]
            )
            body = "".join(lines[1:end])
            try:
                node = yaml.compose(body, Loader=yaml.SafeLoader)
            except yaml.YAMLError as exc:
                raise ToolError(f"Cannot parse Markdown frontmatter: {exc}") from exc
            visited: set[int] = set()

            def keys(node: Node | None) -> None:
                if node is None or id(node) in visited:
                    return
                visited.add(id(node))
                if isinstance(node, MappingNode):
                    for key, value in node.value:
                        spans.append(
                            (
                                len(lines[0]) + key.start_mark.index,
                                len(lines[0]) + key.end_mark.index,
                            )
                        )
                        keys(value)
                elif isinstance(node, SequenceNode):
                    for child in node.value:
                        keys(child)

            keys(node)

    fence: str | None = None
    start = 0
    for index, line in enumerate(lines):
        if index <= frontmatter_end:
            continue
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*?)[\r\n]*$", line)
        if fence is not None:
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= len(fence)
                and not match[2].strip()
            ):
                spans.append((start, offsets[index] + len(line.rstrip("\r\n"))))
                fence = None
        elif match:
            fence = match[1]
            start = offsets[index]
        elif line.startswith(("    ", "\t")):
            spans.append((offsets[index], offsets[index] + len(line.rstrip("\r\n"))))
    if fence is not None:
        spans.append((start, len(text)))

    # Earlier alternatives consume literal code before interpreting links or HTML inside it.
    literals = re.compile(
        r"(?P<code>(?<!`)(?P<ticks>`+)(?!`)[\s\S]*?(?<!`)(?P=ticks)(?!`))"
        r"|(?P<html><!--[\s\S]*?-->|</?[A-Za-z][A-Za-z0-9:-]*"
        r"""(?:\s+(?:[^"'<>]|"[^"]*"|'[^']*')*)?\s*/?>)"""
        r"|(?P<autolink><(?:[A-Za-z][A-Za-z0-9+.-]*:[^<>\s]*|[^<>\s]+@[^<>\s]+)>)"
        r"|(?P<url>(?:[A-Za-z][A-Za-z0-9+.-]*://|mailto:|www\.)[^\s<>]+)"
        r"|(?P<reference>^ {0,3}\[[^\]\r\n]+\]:[ \t]*(?:\r?\n[ \t]+)?[^\r\n]*)"
        r"|(?P<collapsed>!?\[[^\]\r\n]*\]\[\])"
        r"|(?P<label>\]\[[^\]\r\n]*\])"
        r"|(?P<link>\]\()",
        re.MULTILINE,
    )
    position = 0
    while match := literals.search(text, position):
        begin, end = match.span()
        if match.lastgroup == "link":
            begin += 1
            depth = 1
            quote: str | None = None
            angle = False
            end = match.end()
            while end < len(text) and depth:
                char = text[end]
                if char == "\\":
                    end += 2
                    continue
                if quote is not None:
                    if char == quote:
                        quote = None
                elif char in "\"'" and text[end - 1].isspace():
                    quote = char
                elif char == "<":
                    angle = True
                elif char == ">":
                    angle = False
                elif char == "(" and not angle:
                    depth += 1
                elif char == ")" and not angle:
                    depth -= 1
                end += 1
            if depth:
                position = match.end()
                continue
        elif match.lastgroup == "url":
            while end > begin and text[end - 1] in ".,;:!?":
                end -= 1
            while text[begin:end].count(")") > text[begin:end].count("("):
                end -= 1
        spans.append((begin, end))
        position = end

    # A shortcut reference's label is also its target, so it cannot be translated.
    labels = re.findall(r"^ {0,3}\[([^\]\r\n]+)\]:", text, re.MULTILINE)
    for label in labels:
        spans.extend(
            match.span()
            for match in re.finditer(r"\[" + re.escape(label) + r"\](?![\[(:])", text, re.I)
        )

    placeholders: dict[str, str] = {}
    parts: list[str] = []
    position = 0
    for begin, end in sorted(spans, key=lambda span: (span[0], -span[1])):
        if begin < position or begin == end:
            continue
        token = f"{prefix}{len(placeholders)}__"
        placeholders[token] = text[begin:end]
        parts.extend((text[position:begin], token))
        position = end
    parts.append(text[position:])
    return ProtectedMarkdown("".join(parts), placeholders, prefix)


def segments(text: str) -> list[str]:
    """Split at paragraphs and headings, retaining every separator verbatim."""
    result: list[str] = []
    paragraph: list[str] = []
    for line in text.splitlines(keepends=True):
        if paragraph and re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*(?:\r?\n)?", line):
            paragraph.append(line)
            result.append("".join(paragraph))
            paragraph = []
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s", line)
        if not line.strip() or heading:
            if paragraph:
                result.append("".join(paragraph))
                paragraph = []
            result.append(line)
        else:
            paragraph.append(line)
    if paragraph:
        result.append("".join(paragraph))
    return result
