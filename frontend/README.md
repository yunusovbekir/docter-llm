# Docter Frontend

Docter is a Vue 3 single-page app built with Vite and TypeScript. Vue Router
selects the page, Pinia holds shared UI and API state, and composables coordinate
workflows such as indexing, chat, and patch review. The browser calls the
backend; it does not talk directly to MCP, Git, or the model provider.

## Architecture

```mermaid
flowchart TB
    subgraph Frontend["Docter frontend"]
        App["App shell<br/>Vue Router, shared layout, notifications"]
        Projects["Projects and project detail<br/>register, index, start a session"]
        SettingsHistory["Settings and history<br/>configure provider, resume sessions"]
        Session["Session view<br/>chat and change review"]
        Chat["Chat panel<br/>messages, tool cards, coverage"]
        Review["Changes panel<br/>change list, review controls, lazy Monaco diff"]
        Workflows["Workflow composables<br/>index polling, session creation, history, review"]
        ChatRun["useChat<br/>optimistic messages, live events, cancel"]
        State["Pinia stores<br/>projects, settings, session, changes"]
        REST["Typed REST client<br/>openapi-fetch and generated OpenAPI types"]
        SSE["useEventStream<br/>POST JSON, parse typed SSE events"]

        App --> Projects
        App --> SettingsHistory
        App --> Session
        Projects --> Workflows
        SettingsHistory --> Workflows
        Projects --> State
        SettingsHistory --> State
        Session --> State
        Session --> Chat
        Session --> Review
        Review --> Workflows
        Review -->|revision comment via chat| ChatRun
        Chat <--> ChatRun
        ChatRun -->|proposal event triggers reload| Session
        Workflows --> REST
        State --> REST
        ChatRun -->|cancel| REST
        ChatRun <--> SSE
    end

    REST <-->|HTTP and JSON| Backend["Backend API"]
    SSE <-->|POST and streamed events| Backend
```

## How data moves

- **Index and start a session.** The project page starts indexing over REST.
  `useIndexStatus` polls once a second while indexing is running and stops on
  completion, failure, or unmount. Session creation is enabled only after
  indexing succeeds; it opens the new session at `/sessions/:id`.
- **Load and stream chat.** The session store loads saved messages and project
  metadata. `useChat` adds a user message immediately, then uses `useEventStream`
  to append tokens, pair tool calls with results, and display notices. Assistant
  Markdown is rendered with markdown-it and sanitized with DOMPurify. The same
  streaming path handles revision comments; streams do not retry automatically
  and are aborted on unmount.
- **Review a patch.** A `change_proposed` event refreshes the changes store.
  Selecting a file opens its original and proposed snapshots in a read-only
  Monaco diff. The browser remembers the side-by-side or inline layout.
  Approve and deny actions use REST and refresh both the changes and session
  metadata, so an updated branch name appears without remounting chat.

See [Frontend development](../README.md#frontend-development) for setup,
API type generation, and test commands.
