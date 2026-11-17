"""URIs of the MCP resources served by Docter."""

import re
from typing import Final

from docter_contract._base import LANGUAGE_TAG_PATTERN

STYLE_GUIDE_URI: Final = "docter://style-guide"
"""The documentation style guide (Markdown) that generated docs must follow."""

GLOSSARY_URI_TEMPLATE: Final = "docter://glossary/{lang}"
"""URI template of the translation glossary for one target language (a BCP 47 tag)."""

RESOURCE_URIS: Final = (STYLE_GUIDE_URI, GLOSSARY_URI_TEMPLATE)
"""All resource URIs and URI templates, in a stable order."""

_LANGUAGE_TAG = re.compile(LANGUAGE_TAG_PATTERN)


def glossary_uri(lang: str) -> str:
    """Return the glossary resource URI for ``lang``.

    Raises:
        ValueError: If ``lang`` is not a BCP 47 language tag such as ``de`` or ``pt-BR``.
    """
    if not _LANGUAGE_TAG.fullmatch(lang):
        raise ValueError(f"not a BCP 47 language tag: {lang!r}")
    return GLOSSARY_URI_TEMPLATE.format(lang=lang)
