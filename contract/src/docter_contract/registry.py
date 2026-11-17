"""Registry mapping each tool name to its input and output models."""

from dataclasses import dataclass
from types import MappingProxyType

from docter_contract._base import ContractModel
from docter_contract.enums import ToolName
from docter_contract.models import (
    CheckDocDriftInput,
    CheckDocDriftOutput,
    FindDocGapsInput,
    FindDocGapsOutput,
    GetServerInfoInput,
    GetServerInfoOutput,
    GetSymbolInput,
    GetSymbolOutput,
    IndexProgress,
    IndexRepoInput,
    IndexRepoOutput,
    ListDocFilesInput,
    ListDocFilesOutput,
    ProposePatchInput,
    ProposePatchOutput,
    ReadFileInput,
    ReadFileOutput,
    RunDocExamplesInput,
    RunDocExamplesOutput,
    SearchCodeInput,
    SearchCodeOutput,
    TranslateFileInput,
    TranslateFileOutput,
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """The contract of one tool.

    Attributes:
        input: Model of the tool's arguments.
        output: Model of the tool's structured result.
        progress: Model of the progress notifications the tool sends, if it sends any.
    """

    input: type[ContractModel]
    output: type[ContractModel]
    progress: type[ContractModel] | None = None


TOOL_SPECS: MappingProxyType[ToolName, ToolSpec] = MappingProxyType(
    {
        ToolName.GET_SERVER_INFO: ToolSpec(GetServerInfoInput, GetServerInfoOutput),
        ToolName.INDEX_REPO: ToolSpec(IndexRepoInput, IndexRepoOutput, progress=IndexProgress),
        ToolName.SEARCH_CODE: ToolSpec(SearchCodeInput, SearchCodeOutput),
        ToolName.GET_SYMBOL: ToolSpec(GetSymbolInput, GetSymbolOutput),
        ToolName.READ_FILE: ToolSpec(ReadFileInput, ReadFileOutput),
        ToolName.LIST_DOC_FILES: ToolSpec(ListDocFilesInput, ListDocFilesOutput),
        ToolName.FIND_DOC_GAPS: ToolSpec(FindDocGapsInput, FindDocGapsOutput),
        ToolName.CHECK_DOC_DRIFT: ToolSpec(CheckDocDriftInput, CheckDocDriftOutput),
        ToolName.RUN_DOC_EXAMPLES: ToolSpec(RunDocExamplesInput, RunDocExamplesOutput),
        ToolName.PROPOSE_PATCH: ToolSpec(ProposePatchInput, ProposePatchOutput),
        ToolName.TRANSLATE_FILE: ToolSpec(TranslateFileInput, TranslateFileOutput),
    }
)
"""Input, output, and progress models of every tool, keyed by tool name."""
