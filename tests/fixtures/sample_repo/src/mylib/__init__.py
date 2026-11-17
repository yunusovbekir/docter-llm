"""mylib: a tiny client library."""

from mylib.client import Client, connect
from mylib.legacy import mean

__all__ = ["Client", "connect"]
__all__ += ["mean"]


def configure(level: int = 0) -> None:
    """Configure logging for the library.

    Args:
        level: Verbosity level.
    """
