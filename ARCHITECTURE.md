# Argos Architecture

Argos is a small, single-process server that stores the working memory of many
projects (status notes, tasks, decisions, and an activity log) in one SQLite
database and exposes it to AI-agent clients over MCP (Streamable HTTP) plus a few
plain HTTP routes.

## Components

- **FastMCP server (`argos_mcp/server.py`)** - a single Python process built on
  [FastMCP](https://github.com/jlowin/fastmcp). It registers 11 MCP tools and a
  handful of plain HTTP routes, and it owns the SQLite connection lifecycle. The
  `argos` CLI (`argos_mcp/cli.py`) runs it over HTTP (`argos serve`) or stdio
  (`argos stdio`).
- **SQLite database** - one file, opened in WAL mode, with an FTS5 virtual table
  for full-text search. This is the entire persistence layer; there is no
  separate database server.
- **Reverse proxy (Caddy)** - terminates TLS with a Let's Encrypt certificate
  and forwards to the loopback-bound server. Argos itself speaks plain HTTP only
  on `127.0.0.1`.
- **Clients** - one or more AI agents. A remote client (for example Claude Code
  on a laptop) connects over HTTPS; a co-located client on the same host may
  connect directly over localhost.
- **SessionStart hook (`hooks/argos-session-start.sh`)** - a Claude Code hook
  that fetches a project's central status at session start and injects it into
  the model's context.

## Data model

Five tables in one SQLite database:

- **projects** - one row per project: `name` (primary key), `description`,
  `status_md` (the distilled status note, Markdown), `repo_path`, `archived`
  flag, and `updated_at`.
- **tasks** - `id`, `project`, `title`, `status` (`open | in_progress |
  blocked | done`), `priority` (1 high .. 5 low), `notes`, and timestamps.
- **decisions** - `id`, `project`, `decision`, `rationale`, `created_at`. An
  append-only log of durable technical/product decisions.
- **activity** - `id`, `project`, `source` (who acted), `action`, `detail`,
  `created_at`. An append-only audit feed written by the tools.
- **search_idx** - an FTS5 virtual table (`project, kind, ref_id, content`).
  Status notes, tasks, and decisions are indexed into it on write so the
  `search` tool can run full-text queries. Rows are re-indexed in place
  (delete-then-insert by `kind` + `ref_id`).

## Dual surface: MCP tools and plain HTTP

The server exposes two surfaces over the same logic:

- **MCP tools** (`/mcp`) - the 11 `@mcp.tool` functions, consumed by agent
  clients over Streamable HTTP.
- **Plain HTTP routes** - `GET /` (HTML dashboard), `GET /overview`
  (plain-text summary), `GET /status/{name}` (plain-text project status), and
  `GET /health` (`{"ok": true}`, no auth). These serve browsers and shell hooks
  that cannot speak MCP.

### Why the `_impl` functions exist

The core read logic lives in plain functions - `overview_impl` and
`status_impl`. The reason is a constraint of the tool decorator: `@mcp.tool`
wraps a function into a `FunctionTool` object, so the decorated name is no longer
an ordinary callable and the plain HTTP routes cannot invoke it directly.
Keeping the logic in undecorated `_impl` functions lets both surfaces share it:
the MCP tool (`projects_overview`, `project_status_get`) and the HTTP route
(`/overview`, `/status/{name}`) each call the same `_impl` function.

## Auth model

- Authentication is a single static Bearer token, `MC_TOKEN`.
- MCP requests are verified by FastMCP's `StaticTokenVerifier`. If `MC_TOKEN` is
  empty, auth is disabled entirely - only acceptable for localhost-only testing.
- Plain HTTP routes are guarded by `_authorized()`, which accepts either an
  `Authorization: Bearer <token>` header or a `?token=<token>` query parameter
  (the latter lets the dashboard be opened in a browser). `/health` is
  intentionally unauthenticated and returns only `{"ok": true}`.
- TLS is provided by the reverse proxy, not by Argos. The server binds
  `127.0.0.1` and is never exposed directly; the firewall opens only 22, 80, 443.

## Deployment topology

```
                Internet
                   |
                   | HTTPS (443), Bearer token
                   v
          +-----------------+
          |  Caddy (TLS)    |   Let's Encrypt
          +-----------------+
                   | reverse_proxy, plain HTTP
                   v
          +-----------------------+
          |  Argos 127.0.0.1:8765 |   systemd service, non-root 'argos' user
          +-----------------------+
                   |
                   v
             SQLite (WAL + FTS5)   nightly cron backup, 7-day rotation

Co-located client (same host) ---> 127.0.0.1:8765 directly (bypasses Caddy)
```

Process management is via systemd with a hardened unit (`NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths` limited to
the install directory), running as a dedicated non-root `argos` user.

## Request lifecycle

1. A client sends a request. Remote clients hit Caddy on 443; Caddy terminates
   TLS and forwards to `127.0.0.1:8765`. A co-located client connects to
   `127.0.0.1:8765` directly.
2. For MCP calls, FastMCP verifies the Bearer token, deserializes the tool call,
   and dispatches to the matching `@mcp.tool` function. For plain HTTP routes,
   `_authorized()` checks the header or query token first.
3. The tool/route opens a short-lived SQLite connection (WAL mode), runs its
   queries inside a transaction, updates the FTS5 index and the activity log
   where relevant, and closes the connection.
4. The result is returned as text (MCP) or as HTML/plain text/JSON (HTTP).

## Client integration

- **MCP registration** - register the server with the client, for example:

  ```
  claude mcp add --transport http argos https://argos.example.com/mcp \
    --header "Authorization: Bearer <token>"
  ```

- **SessionStart hook** - `hooks/argos-session-start.sh` resolves the project
  name (`.mc-name` at the repo root, else the folder name), calls
  `GET /status/{name}`, and prints the central status so it is injected into the
  new session. It treats a "not found" response as "no record". This sentinel
  must stay in sync with the not-found message returned by `status_impl` in
  `argos_mcp/server.py`.
- **Single writer for status** - a co-located second client is typically given
  read/append tools but not `project_status_set`, so the distilled status note
  keeps a single writer and is not overwritten by two clients at once.
