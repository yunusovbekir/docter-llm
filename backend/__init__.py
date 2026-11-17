"""Docter backend: FastAPI agent host and MCP client."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("docter")
except PackageNotFoundError:  # pragma: no cover - running from an uninstalled checkout
    __version__ = "0.0.0"
