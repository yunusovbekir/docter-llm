"""Chunk documentation into non-overlapping heading sections."""

import re
from itertools import pairwise

from docter_contract import SearchHit, SymbolKind


def doc_chunks(path: str, source: str) -> list[SearchHit]:
    """Keep heading lines and preambles; ignore Markdown headings inside fenced code."""
    lines = source.splitlines(keepends=True)
    starts = {0}
    fence: str | None = None
    rst = path.lower().endswith(".rst")
    for index, line in enumerate(lines):
        text = line.strip()
        if not rst:
            marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
            if marker:
                run, tail = marker.groups()
                if fence is None:
                    fence = run
                elif run[0] == fence[0] and len(run) >= len(fence) and not tail.strip():
                    fence = None
                continue
            if fence is not None:
                continue
            if re.match(r"^ {0,3}#{1,6}\s+\S", line):
                starts.add(index)
        if index + 1 >= len(lines) or not text:
            continue
        underline = lines[index + 1].strip()
        markers = "=-~^\"'`:+*#" if rst else "=-"
        if (
            underline
            and len(set(underline)) == 1
            and underline[0] in markers
            and len(underline) >= len(text)
            and not (len(set(text)) == 1 and text[0] in markers)
        ):
            start = index
            if rst and index > 0 and lines[index - 1].strip() == underline:
                start -= 1
            starts.add(start)
    boundaries = [*sorted(starts), len(lines)]
    return [
        SearchHit(
            file=path,
            kind=SymbolKind.MODULE,
            start_line=start + 1,
            end_line=end,
            snippet="".join(lines[start:end]),
            score=0,
        )
        for start, end in pairwise(boundaries)
        if "".join(lines[start:end]).strip()
    ]
