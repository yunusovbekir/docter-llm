"""Translation sampling, literal integrity, fallback, and read-only patch integration."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from mcp.client.session import ClientRequestContext
from mcp.types import (
    METHOD_NOT_FOUND,
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    ImageContent,
    TextContent,
)
from test_foundation import connected, error_text
from test_patching import assert_applies, snapshot

from docter_contract import ProposePatchOutput, TranslateFileOutput
from docter_mcp.translation.markdown import protect, segments


class Sampler:
    def __init__(self, transform: Callable[[str, int], str] | None = None) -> None:
        self.calls: list[CreateMessageRequestParams] = []
        self.transform = transform or (
            lambda text, attempt: text.replace("Hello", "Hallo").replace("client", "Kunde")
        )

    async def __call__(
        self, context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> CreateMessageResult:
        self.calls.append(params)
        (message,) = params.messages
        assert isinstance(message.content, TextContent)
        return CreateMessageResult(
            model="fake",
            role="assistant",
            content=TextContent(
                type="text", text=self.transform(message.content.text, len(self.calls))
            ),
        )


def glossary(repo: Path) -> None:
    (repo / ".docter").mkdir(exist_ok=True)
    (repo / ".docter" / "glossary.yaml").write_text(
        "de:\n  client: Kunde\n  Docter: Docter\n", encoding="utf-8"
    )


async def translate(
    repo: Path, source: str, sampler: Sampler, path: str = "docs/index.md"
) -> TranslateFileOutput:
    glossary(repo)
    file = repo / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(source.encode())
    before = snapshot(repo)
    async with connected(repo, sampling_callback=sampler) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": path, "target_language": "de"},
        )
    assert not result.is_error, result.content
    assert snapshot(repo) == before
    return TranslateFileOutput.model_validate(result.structured_content)


def assert_translation_applies(repo: Path, output: TranslateFileOutput, expected: str) -> None:
    assert_applies(
        repo,
        ProposePatchOutput(
            file_path=output.target_path,
            patch=output.patch,
            rationale=output.rationale,
            is_new_file=output.is_new_file,
        ),
        expected.encode(),
    )


async def test_sampling_glossary_segments_and_new_patch(repo: Path) -> None:
    sampler = Sampler()
    source = "# Hello\n\nHello client Docter.\n\n## Hello\nHello again."
    output = await translate(repo, source, sampler)
    assert output.source_path == "docs/index.md"
    assert output.target_path == "docs/index.de.md"
    assert output.is_new_file
    assert output.warnings == []
    assert len(sampler.calls) == 4
    for call in sampler.calls:
        assert call.system_prompt is not None
        assert "de" in call.system_prompt
        assert "client: Kunde" in call.system_prompt
        assert "Docter: Docter" in call.system_prompt
        assert "exactly once" in call.system_prompt
    assert_translation_applies(
        repo, output, source.replace("Hello", "Hallo").replace("client", "Kunde")
    )


async def test_protects_all_literals_but_translates_frontmatter_values(repo: Path) -> None:
    source = (
        "---\ntitle: Hello\nnested:\n  client: Hello\n"
        "options: {client: Hello}\n---\n\n"
        "# Hello\n\nHello `Hello client` and ``Hello `client` ``.\n\n"
        "```python\nHello client\n\n# Hello\n```\n\n"
        "~~~~text\nHello client\n~~~\n~~~~\n\n"
        "    Hello client\n\n"
        'Hello [Hello](../client(Hello).md "Hello") and [Hello][client].\n\n'
        '[client]: ../Hello.md "Hello"\n\nHello [client].\n\n'
        'Hello <span title="Hello client">Hello</span> <!-- Hello client -->.\n\n'
        "Hello https://example.com/Hello?q=client and <mailto:client@example.com>.\n"
    )
    sampler = Sampler()
    output = await translate(repo, source, sampler)
    expected = source.replace("title: Hello", "title: Hallo")
    expected = expected.replace("client: Hello", "client: Hallo")
    expected = expected.replace("# Hello\n", "# Hallo\n", 1)
    expected = expected.replace("Hello `Hello", "Hallo `Hello")
    expected = expected.replace("Hello [Hello]", "Hallo [Hallo]")
    expected = expected.replace("and [Hello][client]", "and [Hallo][client]")
    expected = expected.replace("Hello [client].", "Hallo [client].")
    expected = expected.replace("Hello <span", "Hallo <span").replace(
        ">Hello</span>", ">Hallo</span>"
    )
    expected = expected.replace("Hello https:", "Hallo https:")
    assert output.warnings == []
    assert_translation_applies(repo, output, expected)


@pytest.mark.parametrize(
    "source",
    [
        "Hello `x` and ``a ` b``.",
        "```python\r\n# Hello\r\n\r\nx = 'hello'\r\n```\r\n",
        "~~~\nHello\n",
        "<div\n title='Hello'>Hello</div>",
        "[Hello](local(a(b)).md) [Hello][id]\n\n[id]: /hello\n\n[id]",
        '[Hello](local.md "Hello (title") [client][]\n\n[client]: /Hello',
        "[Hello](<../Hello (file).md>)\n\n[id]:\n  ../Hello.md",
        "---\n'Hello': World\nnested: {Hello: World}\n---\n",
        "Hello https://example.com/a(b).",
        "    Hello\n\tHello\n",
    ],
)
def test_lossless_protection_and_segmentation(source: str) -> None:
    protected = protect(source)
    assert "".join(segments(protected.text)) == protected.text
    assert protected.restore(protected.text, protected.text) == source
    assert "".join(protected.restore(part, part) for part in segments(protected.text)) == source


@pytest.mark.parametrize("damage", ["missing", "duplicate", "invented", "malformed"])
def test_placeholder_validation(damage: str) -> None:
    protected = protect("Hello `client`.")
    token = next(iter(protected.placeholders))
    changed = {
        "missing": protected.text.replace(token, ""),
        "duplicate": protected.text + token,
        "invented": protected.text + f"{protected.prefix}999__",
        "malformed": protected.text + protected.prefix + "broken__",
    }[damage]
    with pytest.raises(ValueError):
        protected.restore(protected.text, changed)


@pytest.mark.parametrize("recovers", [True, False])
async def test_retry_and_warning_keep_original_segment(repo: Path, recovers: bool) -> None:
    def transform(text: str, attempt: int) -> str:
        translated = text.replace("Hello", "Hallo")
        if attempt == 1 or not recovers:
            return re.sub(r"__DOCTER_[a-f0-9]+_\d+__", "", translated)
        return translated

    sampler = Sampler(transform)
    source = "Hello `client`.\n\nHello again.\n"
    output = await translate(repo, source, sampler)
    assert len(sampler.calls) == 3
    assert "Retry:" in (sampler.calls[1].system_prompt or "")
    assert len(output.warnings) == (0 if recovers else 1)
    if not recovers:
        assert "Segment 1" in output.warnings[0]
        assert "kept source text" in output.warnings[0]
    expected = ("Hallo `client`." if recovers else "Hello `client`.") + "\n\nHallo again.\n"
    assert_translation_applies(repo, output, expected)


async def test_empty_sampling_response_retries(repo: Path) -> None:
    sampler = Sampler(lambda text, attempt: "")
    output = await translate(repo, "Hello.\n", sampler)
    assert len(sampler.calls) == 2
    assert len(output.warnings) == 1
    assert_translation_applies(repo, output, "Hello.\n")


async def test_existing_translation_is_updated(repo: Path) -> None:
    (repo / "docs" / "index.de.md").write_text("Old translation\n")
    output = await translate(repo, "Hello client.\r\n", Sampler())
    assert not output.is_new_file
    assert "-Old translation" in output.patch
    assert_translation_applies(repo, output, "Hallo Kunde.\r\n")


@pytest.mark.parametrize(
    ("config", "source", "expected"),
    [
        ("plugins:\n - i18n\n", "docs/index.md", "docs/de/index.md"),
        ("plugins:\n - i18n\n", "docs/en/index.md", "docs/de/index.md"),
        ("plugins:\n - i18n\n", "docs/api/index.md", "docs/de/api/index.md"),
        ("site_name: Test\n", "docs/en/index.md", "docs/de/index.md"),
        (
            "plugins:\n - i18n:\n     languages:\n"
            "      - locale: en\n        default: true\n      - locale: de\n",
            "docs/en/guide/start.md",
            "docs/de/guide/start.md",
        ),
        ("plugins:\n  i18n:\n    docs_structure: folder\n", "docs/index.md", "docs/de/index.md"),
        ("plugins:\n - i18n:\n     docs_structure: suffix\n", "docs/index.md", "docs/index.de.md"),
        ("plugins:\n - i18n\n", "README.md", "README.de.md"),
        ("docs_dir: manual\nplugins:\n - i18n\n", "manual/index.md", "manual/de/index.md"),
    ],
)
async def test_i18n_target_layout(repo: Path, config: str, source: str, expected: str) -> None:
    (repo / "mkdocs.yml").write_text(config)
    output = await translate(repo, "Hello.\n", Sampler(), source)
    assert output.target_path == expected
    assert_translation_applies(repo, output, "Hallo.\n")


async def test_sampling_unavailable_is_contract_error(repo: Path) -> None:
    async with connected(repo) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "README.md", "target_language": "de"},
        )
    assert "SAMPLING_UNAVAILABLE" in error_text(result)


async def test_unimplemented_sampling_is_contract_error(repo: Path) -> None:
    glossary(repo)

    async def unsupported(
        context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> ErrorData:
        return ErrorData(code=METHOD_NOT_FOUND, message="Sampling not implemented")

    async with connected(repo, sampling_callback=unsupported) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "README.md", "target_language": "de"},
        )
    assert "SAMPLING_UNAVAILABLE" in error_text(result)


@pytest.mark.parametrize("glossary_text", [None, "fr: {}\n", "de: []\n", "de: ["])
async def test_glossary_errors_are_not_silently_ignored(
    repo: Path, glossary_text: str | None
) -> None:
    if glossary_text is not None:
        glossary(repo)
        (repo / ".docter" / "glossary.yaml").write_text(glossary_text)
    sampler = Sampler()
    async with connected(repo, sampling_callback=sampler) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "README.md", "target_language": "de"},
        )
    assert result.is_error
    assert sampler.calls == []


async def test_target_symlink_cannot_escape(repo: Path, tmp_path: Path) -> None:
    glossary(repo)
    outside = tmp_path / "outside.md"
    outside.write_text("Secret")
    (repo / "README.de.md").symlink_to(outside)
    sampler = Sampler()
    async with connected(repo, sampling_callback=sampler) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "README.md", "target_language": "de"},
        )
    assert "PATH_OUTSIDE_REPO" in error_text(result)
    assert sampler.calls == []
    assert outside.read_text() == "Secret"


async def test_literal_only_file_does_not_sample(repo: Path) -> None:
    sampler = Sampler()
    source = "```python\nprint('Hello')\n```\n"
    output = await translate(repo, source, sampler)
    assert sampler.calls == []
    assert output.warnings == []
    assert_translation_applies(repo, output, source)


async def test_nested_frontmatter_values_are_not_mistaken_for_indented_code(repo: Path) -> None:
    source = "---\nouter:\n  inner:\n    title: Hello\n---\n"
    output = await translate(repo, source, Sampler())
    assert_translation_applies(repo, output, source.replace("Hello", "Hallo"))


async def test_quoted_and_collapsed_link_targets_are_protected(repo: Path) -> None:
    source = (
        'Hello [Hello](../client.md "Hello (title").\n\n'
        "Hello [client][] and [Hello](<../client (file).md>).\n\n"
        "[client]:\n  ../Hello.md\n"
    )
    output = await translate(repo, source, Sampler())
    expected = (
        'Hallo [Hallo](../client.md "Hello (title").\n\n'
        "Hallo [client][] and [Hallo](<../client (file).md>).\n\n"
        "[client]:\n  ../Hello.md\n"
    )
    assert_translation_applies(repo, output, expected)


def test_frontmatter_keys_remain_yaml_keys() -> None:
    source = "---\ntitle: Hello\nnested:\n  client: Hello\n---\n"
    protected = protect(source)
    result = protected.restore(protected.text, protected.text.replace("Hello", "Hallo"))
    assert yaml.safe_load(result.split("---")[1]) == {
        "title": "Hallo",
        "nested": {"client": "Hallo"},
    }


def test_segments_split_both_heading_styles_without_blank_lines() -> None:
    source = "Hello\n=====\nParagraph\n## Heading\nNext paragraph\n"
    assert segments(source) == ["Hello\n=====\n", "Paragraph\n", "## Heading\n", "Next paragraph\n"]


@pytest.mark.parametrize("kind", ["truncated", "image"])
async def test_invalid_sampling_result_keeps_source_with_warning(repo: Path, kind: str) -> None:
    glossary(repo)
    calls = 0

    async def invalid(
        context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> CreateMessageResult:
        nonlocal calls
        calls += 1
        return CreateMessageResult(
            model="fake",
            role="assistant",
            content=(
                TextContent(type="text", text="Truncated")
                if kind == "truncated"
                else ImageContent(type="image", data="AA==", mime_type="image/png")
            ),
            stop_reason="maxTokens" if kind == "truncated" else "endTurn",
        )

    (repo / "docs" / "index.md").write_text("Hello.\n")
    async with connected(repo, sampling_callback=invalid) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "docs/index.md", "target_language": "de"},
        )
    assert not result.is_error
    output = TranslateFileOutput.model_validate(result.structured_content)
    assert calls == 2
    assert len(output.warnings) == 1
    assert_translation_applies(repo, output, "Hello.\n")


async def test_provider_error_fails_explicitly_without_retrying(repo: Path) -> None:
    glossary(repo)
    calls = 0

    async def unavailable(
        context: ClientRequestContext, params: CreateMessageRequestParams
    ) -> ErrorData:
        nonlocal calls
        calls += 1
        return ErrorData(code=-32603, message="Model provider unavailable")

    async with connected(repo, sampling_callback=unavailable) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "README.md", "target_language": "de"},
        )
    assert "Model provider unavailable" in error_text(result)
    assert calls == 1


async def test_existing_translation_can_be_unchanged(repo: Path) -> None:
    (repo / "docs" / "index.de.md").write_text("Hallo.\n")
    output = await translate(repo, "Hello.\n", Sampler())
    assert output.patch == ""
    assert not output.is_new_file
    assert output.warnings == []


async def test_i18n_source_already_in_target_language_is_not_overwritten(repo: Path) -> None:
    glossary(repo)
    (repo / "mkdocs.yml").write_text("plugins:\n - i18n\n")
    (repo / "docs" / "de").mkdir()
    (repo / "docs" / "de" / "index.md").write_text("Hallo.\n")
    sampler = Sampler()
    async with connected(repo, sampling_callback=sampler) as session:
        result = await session.call_tool(
            "translate_file",
            {"repo": str(repo), "file_path": "docs/de/index.md", "target_language": "de"},
        )
    assert "target resolves to the source file" in error_text(result)
    assert sampler.calls == []
