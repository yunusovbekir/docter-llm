"""Client for talking to a remote service."""

from __future__ import annotations

import functools
from typing import Any

DEFAULT_TIMEOUT = 1.0


class ClientError(Exception):
    """Raised when the remote service reports an error."""


class Client:
    """A connection to a remote service.

    Args:
        host: Host name of the service.
        port: TCP port of the service.
    """

    def __init__(self, host: str, port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._name = host

    @property
    def name(self) -> str:
        """The display name of the client."""
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        if not value:
            raise ValueError("name must not be empty")
        self._name = value

    @functools.cached_property
    def address(self) -> str:
        """The ``host:port`` address of the service."""
        return f"{self.host}:{self.port}"

    def send(
        self,
        data: bytes,
        /,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = 3,
    ) -> int:
        """Send data to the service.

        Args:
            data: Payload to send.
            timeout: Seconds to wait for an answer.
            retries: How often to retry.

        Returns:
            Number of bytes sent.

        Raises:
            ValueError: If ``data`` is empty.
            ClientError: If the service rejects the payload.
        """
        if not data:
            raise ValueError("data must not be empty")
        try:
            sent = self._write(data)
        except OSError as exc:
            raise ClientError(str(exc)) from exc
        except KeyError:
            raise
        return sent

    async def fetch(self, path: str, *segments: str, **headers: Any) -> bytes:
        """Fetch a resource.

        Args:
            path: Resource path.
            *segments: Extra path segments.
            **headers: HTTP headers.

        Returns:
            The response body.
        """

        def join() -> str:
            raise RuntimeError("raised by a nested function, not by fetch")

        return join().encode()

    @staticmethod
    def parse_url(url: str) -> tuple[str, int]:
        host, _, port = url.partition(":")
        return host, int(port or 80)

    @classmethod
    def from_url(cls, url: str) -> Client:
        """Create a client from a ``host:port`` URL.

        Args:
            address: The URL to parse.
        """
        return cls(*cls.parse_url(url))

    def _write(self, data: bytes) -> int:
        try:
            return len(data)
        except TypeError as exc:
            raise exc

    class Options:
        """Connection options."""

        def as_dict(self) -> dict[str, Any]:
            """Return the options as a dictionary."""
            return {}


def connect(host: str, port: int = 8080, *, secure: bool = False) -> Client:
    """Connect to a service.

    Args:
        host: Host name.
        port: TCP port.

    Returns:
        A connected client.
    """
    return Client(host, port)


def ping(host, timeout=None):
    return True


def _helper() -> None:
    """Private helper."""


class Undocumented:
    pass
