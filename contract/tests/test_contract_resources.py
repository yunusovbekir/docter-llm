import pytest

from docter_contract import (
    GLOSSARY_URI_TEMPLATE,
    RESOURCE_URIS,
    STYLE_GUIDE_URI,
    glossary_uri,
)


def test_resource_uris() -> None:
    assert STYLE_GUIDE_URI == "docter://style-guide"
    assert GLOSSARY_URI_TEMPLATE == "docter://glossary/{lang}"
    assert RESOURCE_URIS == (STYLE_GUIDE_URI, GLOSSARY_URI_TEMPLATE)


@pytest.mark.parametrize("lang", ["de", "pt-BR", "zh-Hans"])
def test_glossary_uri(lang: str) -> None:
    assert glossary_uri(lang) == f"docter://glossary/{lang}"


@pytest.mark.parametrize("lang", ["", "German", "de/../x", "pt_BR"])
def test_glossary_uri_rejects_invalid_tags(lang: str) -> None:
    with pytest.raises(ValueError, match="BCP 47"):
        glossary_uri(lang)
