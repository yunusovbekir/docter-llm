"""Launch the real Docter MCP CLI with deterministic, offline embeddings."""

from unittest.mock import patch

from docter_mcp.server import main


def documents(texts: list[str]) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


if __name__ == "__main__":
    with patch("docter_mcp.indexing.service.Embedder.documents", side_effect=documents):
        main()
