"""Extract Python symbols with the standard library ``ast`` module."""

import ast
import warnings
from collections.abc import Iterator, Sequence
from pathlib import PurePosixPath

from docter_contract import GetSymbolOutput, ParameterKind, SymbolKind, SymbolParameter
from docter_mcp.parsing.base import ParseResult, ParseWarning
from docter_mcp.parsing.config import ParsingSettings

type _Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def module_name(path: str) -> str:
    """Return the dotted module name of a repo-relative ``.py`` path.

    A leading ``src/`` directory is dropped and ``__init__`` stands for its package, so
    ``src/mylib/client.py`` is ``mylib.client`` and ``src/mylib/__init__.py`` is ``mylib``.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    if len(parts) > 1 and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def is_private_name(name: str) -> bool:
    """Whether a name is private: it starts with ``_`` and is not a dunder like ``__init__``."""
    is_dunder = len(name) > 4 and name.startswith("__") and name.endswith("__")
    return name.startswith("_") and not is_dunder


class PythonParser:
    """``LanguageParser`` for Python source files.

    Emits the module itself, its classes (including nested classes), module-level functions,
    methods, and properties, in source order. Functions defined inside functions are not
    symbols. Definitions inside module- or class-level ``if``, ``try``, and ``with`` blocks
    are included; when a qualified name is defined twice (for example ``typing.overload``
    stubs), the later definition wins. Property setters and deleters are not emitted
    separately: the property symbol describes its getter.
    """

    language = "python"
    extensions = frozenset({".py"})

    def parse(self, path: str, source: str) -> ParseResult:
        """Extract the symbols of one Python file. See ``LanguageParser.parse``."""
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            return _skipped(path, exc.lineno, f"invalid Python syntax ({exc.msg})")
        except (ValueError, RecursionError) as exc:
            # ValueError: text that cannot be encoded, such as lone surrogates.
            return _skipped(path, None, f"cannot parse the file ({exc})")
        try:
            symbols = _Extractor(path, source).extract(tree)
        except RecursionError:
            return _skipped(path, None, "the file is nested too deeply to analyze")
        return ParseResult(symbols=tuple(symbols))


def _skipped(path: str, line: int | None, reason: str) -> ParseResult:
    return ParseResult(warnings=(ParseWarning(path=path, line=line, message=f"Skipped: {reason}"),))


class _Extractor:
    def __init__(self, path: str, source: str) -> None:
        self._path = path
        self._source = source
        self._lines: list[str] = ParsingSettings.LINE_PATTERN.findall(source)
        self._symbols: dict[str, GetSymbolOutput] = {}

    def extract(self, tree: ast.Module) -> list[GetSymbolOutput]:
        module = module_name(self._path)
        module_public = not any(is_private_name(part) for part in module.split("."))
        self._symbols[module] = GetSymbolOutput(
            qualname=module,
            kind=SymbolKind.MODULE,
            file=self._path,
            start_line=1,
            end_line=max(1, len(self._lines)),
            signature="",
            parameters=[],
            returns=None,
            raises=[],
            decorators=[],
            docstring=ast.get_docstring(tree),
            is_public=module_public,
            source=self._source,
        )
        exported = _static_all(tree.body)
        for node in _definitions(tree.body):
            if exported is not None:
                public = node.name in exported
            else:
                public = not is_private_name(node.name)
            self._visit(node, module, module_public and public, in_class=False)
        return list(self._symbols.values())

    def _visit(self, node: _Definition, parent: str, public: bool, *, in_class: bool) -> None:
        qualname = f"{parent}.{node.name}"
        if isinstance(node, ast.ClassDef):
            self._symbols[qualname] = self._class_symbol(node, qualname, public)
            for child in _definitions(node.body):
                child_public = public and not is_private_name(child.name)
                self._visit(child, qualname, child_public, in_class=True)
            return
        if in_class and _is_property_accessor(node):
            return
        if not in_class:
            kind = SymbolKind.FUNCTION
        elif _is_property(node):
            kind = SymbolKind.PROPERTY
        else:
            kind = SymbolKind.METHOD
        self._symbols[qualname] = self._function_symbol(node, qualname, kind, public)

    def _class_symbol(self, node: ast.ClassDef, qualname: str, public: bool) -> GetSymbolOutput:
        bases = [self._segment(base) for base in node.bases]
        bases += [self._segment(keyword) for keyword in node.keywords]
        signature = f"class {node.name}{self._type_params(node)}"
        if bases:
            signature += f"({', '.join(bases)})"
        return self._symbol(
            node,
            qualname=qualname,
            kind=SymbolKind.CLASS,
            signature=signature,
            parameters=[],
            returns=None,
            raises=[],
            public=public,
        )

    def _function_symbol(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        qualname: str,
        kind: SymbolKind,
        public: bool,
    ) -> GetSymbolOutput:
        parameters = self._parameters(node.args)
        returns = self._segment(node.returns) if node.returns is not None else None
        keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        signature = (
            f"{keyword} {node.name}{self._type_params(node)}({format_parameters(parameters)})"
        )
        if returns is not None:
            signature += f" -> {returns}"
        return self._symbol(
            node,
            qualname=qualname,
            kind=kind,
            signature=signature,
            parameters=parameters,
            returns=returns,
            raises=_raised_names(node.body),
            public=public,
        )

    def _symbol(
        self,
        node: _Definition,
        *,
        qualname: str,
        kind: SymbolKind,
        signature: str,
        parameters: list[SymbolParameter],
        returns: str | None,
        raises: list[str],
        public: bool,
    ) -> GetSymbolOutput:
        start_line = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        end_line = node.end_lineno or node.lineno
        return GetSymbolOutput(
            qualname=qualname,
            kind=kind,
            file=self._path,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            parameters=parameters,
            returns=returns,
            raises=raises,
            decorators=[self._segment(decorator) for decorator in node.decorator_list],
            docstring=ast.get_docstring(node),
            is_public=public,
            source="".join(self._lines[start_line - 1 : end_line]),
        )

    def _parameters(self, args: ast.arguments) -> list[SymbolParameter]:
        positional = [*args.posonlyargs, *args.args]
        missing = len(positional) - len(args.defaults)
        defaults: list[ast.expr | None] = [None] * missing + list(args.defaults)
        result: list[SymbolParameter] = []
        for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
            if index < len(args.posonlyargs):
                kind = ParameterKind.POSITIONAL_ONLY
            else:
                kind = ParameterKind.POSITIONAL_OR_KEYWORD
            result.append(self._parameter(arg, default, kind))
        if args.vararg is not None:
            result.append(self._parameter(args.vararg, None, ParameterKind.VAR_POSITIONAL))
        for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=True):
            result.append(self._parameter(arg, default, ParameterKind.KEYWORD_ONLY))
        if args.kwarg is not None:
            result.append(self._parameter(args.kwarg, None, ParameterKind.VAR_KEYWORD))
        return result

    def _parameter(
        self, arg: ast.arg, default: ast.expr | None, kind: ParameterKind
    ) -> SymbolParameter:
        return SymbolParameter(
            name=arg.arg,
            annotation=self._segment(arg.annotation) if arg.annotation is not None else None,
            default=self._segment(default) if default is not None else None,
            kind=kind,
        )

    def _type_params(self, node: _Definition) -> str:
        if not node.type_params:
            return ""
        return f"[{', '.join(self._segment(param) for param in node.type_params)}]"

    def _segment(self, node: ast.expr | ast.keyword | ast.type_param) -> str:
        """Return a node's source text; a node spanning several lines is unparsed onto one."""
        if node.end_lineno != node.lineno or node.end_col_offset is None:
            return ast.unparse(node)
        line = self._lines[node.lineno - 1].encode()
        # ast column offsets count UTF-8 bytes, not characters.
        return line[node.col_offset : node.end_col_offset].decode()


def format_parameters(parameters: Sequence[SymbolParameter]) -> str:
    """Format parameters as a Python parameter list, with ``/`` and ``*`` markers.

    For example ``data: bytes, /, *, timeout: float = 1.0``.
    """
    parts: list[str] = []
    for index, parameter in enumerate(parameters):
        previous = parameters[index - 1].kind if index > 0 else None
        if parameter.kind is ParameterKind.KEYWORD_ONLY and previous not in (
            ParameterKind.KEYWORD_ONLY,
            ParameterKind.VAR_POSITIONAL,
        ):
            parts.append("*")
        if parameter.kind is ParameterKind.VAR_POSITIONAL:
            text = f"*{parameter.name}"
        elif parameter.kind is ParameterKind.VAR_KEYWORD:
            text = f"**{parameter.name}"
        else:
            text = parameter.name
        if parameter.annotation is not None:
            text += f": {parameter.annotation}"
            if parameter.default is not None:
                text += f" = {parameter.default}"
        elif parameter.default is not None:
            text += f"={parameter.default}"
        parts.append(text)
        following = parameters[index + 1].kind if index + 1 < len(parameters) else None
        if (
            parameter.kind is ParameterKind.POSITIONAL_ONLY
            and following is not ParameterKind.POSITIONAL_ONLY
        ):
            parts.append("/")
    return ", ".join(parts)


def _definitions(body: Sequence[ast.stmt]) -> Iterator[_Definition]:
    """Yield the classes and functions of a block, including those in if/try/with blocks."""
    for statement in body:
        if isinstance(statement, ParsingSettings.DEFINITION_TYPES):
            yield statement
        elif isinstance(statement, ast.If):
            yield from _definitions(statement.body)
            yield from _definitions(statement.orelse)
        elif isinstance(statement, ast.Try | ast.TryStar):
            yield from _definitions(statement.body)
            for handler in statement.handlers:
                yield from _definitions(handler.body)
            yield from _definitions(statement.orelse)
            yield from _definitions(statement.finalbody)
        elif isinstance(statement, ast.With | ast.AsyncWith):
            yield from _definitions(statement.body)


def _static_all(body: Sequence[ast.stmt]) -> frozenset[str] | None:
    """Return the names in a module's ``__all__``, or None if it is absent or not static.

    Supports ``__all__ = [...]`` (also annotated or as a tuple) and ``__all__ += [...]`` with
    string literals.
    """
    names: list[str] | None = None
    for statement in body:
        assignment = _all_assignment(statement)
        if assignment is None:
            continue
        value, extend = assignment
        strings = _string_literals(value)
        if strings is None:
            return None
        names = [*(names or []), *strings] if extend else strings
    return frozenset(names) if names is not None else None


def _all_assignment(statement: ast.stmt) -> tuple[ast.expr, bool] | None:
    """Return the value assigned to ``__all__`` and whether it extends it, if any."""
    match statement:
        case ast.Assign(targets=targets, value=value) if any(map(_is_all, targets)):
            return value, False
        case ast.AnnAssign(target=target, value=ast.expr() as value) if _is_all(target):
            return value, False
        case ast.AugAssign(target=target, op=ast.Add(), value=value) if _is_all(target):
            return value, True
    return None


def _is_all(target: ast.expr) -> bool:
    return isinstance(target, ast.Name) and target.id == "__all__"


def _string_literals(node: ast.expr) -> list[str] | None:
    if not isinstance(node, ast.List | ast.Tuple):
        return None
    strings: list[str] = []
    for element in node.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            return None
        strings.append(element.value)
    return strings


def _is_property(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_dotted_name(d) in ParsingSettings.PROPERTY_DECORATORS for d in node.decorator_list)


def _is_property_accessor(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether the function is a ``@<name>.setter``, ``.getter``, or ``.deleter``."""
    return any(
        isinstance(d, ast.Attribute)
        and d.attr in ParsingSettings.PROPERTY_ACCESSORS
        and isinstance(d.value, ast.Name)
        and d.value.id == node.name
        for d in node.decorator_list
    )


def _raised_names(body: Sequence[ast.stmt]) -> list[str]:
    """Return the exception names raised directly in a function body, in source order.

    Skips nested functions and classes, bare ``raise``, re-raising an ``except ... as name``
    alias, and expressions on ``self``/``cls`` (such as ``raise self.error()``).
    """
    nodes = list(_scope_walk(body))
    aliases = {node.name for node in nodes if isinstance(node, ast.ExceptHandler) and node.name}
    names: dict[str, None] = {}
    for node in nodes:
        if not isinstance(node, ast.Raise) or node.exc is None:
            continue
        target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        name = _dotted_name(target)
        if (
            name is None
            or name in aliases
            or name.split(".", 1)[0] in ParsingSettings.INSTANCE_NAMES
        ):
            continue
        names[name] = None
    return list(names)


def _scope_walk(body: Sequence[ast.stmt]) -> Iterator[ast.AST]:
    """Walk a block in source order without descending into nested scopes."""
    stack: list[ast.AST] = [
        s for s in reversed(body) if not isinstance(s, ParsingSettings.SCOPE_TYPES)
    ]
    while stack:
        node = stack.pop()
        yield node
        children = [
            c for c in ast.iter_child_nodes(node) if not isinstance(c, ParsingSettings.SCOPE_TYPES)
        ]
        stack.extend(reversed(children))


def _dotted_name(node: ast.expr) -> str | None:
    """Return ``a.b.c`` for a name or attribute chain, else None."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))
