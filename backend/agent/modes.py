"""Mode-specific prompts, tool allowlists, and required parameters."""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path

import anyio
from pydantic import TypeAdapter, ValidationError

from backend.errors import DocterError
from docter_contract._base import LanguageTag


class InvalidModeParamsError(DocterError):
    """Required mode parameters are missing or invalid."""

    status_code = 422
    code = "invalid_mode_params"


class ModePromptError(DocterError):
    """A packaged system prompt could not be loaded."""

    status_code = 500
    code = "mode_prompt_error"


class Mode(StrEnum):
    """Documentation tasks with explicit tool permissions and parameter requirements."""

    ADD = "add"
    FIX = "fix"
    FIND_MISSING = "find_missing"
    TRANSLATE = "translate"

    @property
    def system_prompt_path(self) -> Path:
        """Return the packaged prompt path, independent of the working directory."""
        return Path(__file__).parent / "prompts" / f"{self.value}.md"

    @property
    def allowed_tools(self) -> list[str]:
        """Return a fresh allowlist; unlisted tools must not be exposed or executed."""
        tools = ["get_server_info", "search_code", "get_symbol", "read_file", "list_doc_files"]
        match self:
            case Mode.ADD:
                tools.extend(["find_doc_gaps", "propose_patch"])
            case Mode.FIX:
                tools.extend(["check_doc_drift", "run_doc_examples", "propose_patch"])
            case Mode.FIND_MISSING:
                tools.append("find_doc_gaps")
            case Mode.TRANSLATE:
                tools.extend(["translate_file", "propose_patch"])
        return tools

    @property
    def required_params(self) -> list[str]:
        """Return the names of parameters required in addition to the task context."""
        return ["target_language"] if self is Mode.TRANSLATE else []

    def validate_params(self, params: Mapping[str, object]) -> None:
        """Validate translation's language tag using the shared tool contract."""
        if self is Mode.TRANSLATE:
            try:
                TypeAdapter(LanguageTag).validate_python(params.get("target_language"), strict=True)
            except ValidationError as exc:
                raise InvalidModeParamsError(
                    f"Mode '{self.value}' requires a language tag for 'target_language' "
                    "(e.g. 'ja', 'pt-BR', or 'zh-Hans')"
                ) from exc

    async def load_system_prompt(self) -> str:
        """Read the UTF-8 prompt asynchronously, failing explicitly if unavailable."""
        try:
            prompt = await anyio.Path(self.system_prompt_path).read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise ModePromptError(
                f"Unable to load the system prompt for mode '{self.value}'"
            ) from exc
        if not prompt.strip():
            raise ModePromptError(f"The system prompt for mode '{self.value}' is empty")
        return prompt
