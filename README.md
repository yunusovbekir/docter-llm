# Docter `beta`

Docter is an AI agent that runs on your own machine and works on the documentation of open-source libraries and SDKs. Point it at a local repository and it can:

- **Find missing docs:** produce a coverage report of public APIs that have no documentation.
- **Add docs:** write docstrings and API reference pages.
- **Fix docs:** find docstrings that no longer match the code, broken links, and examples that fail to run.
- **Translate docs:** translate files into another language while keeping code blocks and Markdown structure intact.

## How it works

1. Select a local git repository and choose a mode.
2. Chat with the agent to give it direction and context.
3. Review each proposed change as a diff, then approve it, deny it, or comment on it. The agent revises the change based on your comments.
4. Docter commits approved changes to a dedicated branch. Your working tree is never modified directly.

Docter works with OpenAI, Anthropic, Ollama, and any other provider supported by [LiteLLM](https://github.com/BerriAI/litellm). API keys are stored in your OS keyring, never on disk.

## Quick start

```bash
git clone <repo-url> && cd docter
uv sync
uv run docter   # http://127.0.0.1:8000
```

Open the app, set your LLM provider and API key under **Settings**, and add a project.

## Documentation

- [Backend](backend/README.md): configuration, API reference, and development guide.
