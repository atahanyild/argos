# Argos

**Argos Panoptes** - the all-seeing memory for your AI agents.

Central project memory: status notes, tasks, decisions, and an activity feed for
all of your projects, collected in a single MCP server backed by one SQLite
database. Multiple AI-agent clients (for example, Claude Code on a laptop and a
second co-located agent on the server) read from and write to the same memory.

Argos binds to `127.0.0.1:8765` and runs behind a reverse proxy (Caddy) that
terminates TLS. Remote clients connect over HTTPS with a single Bearer token; a
client on the same host may connect directly over localhost.

```
Laptop (Claude Code, N projects) ---HTTPS + Bearer token---> Caddy (:443, TLS)
                                                                 |
                                                                 v  reverse_proxy
                                                       Argos (127.0.0.1:8765) ---> SQLite (WAL + FTS5)
                                                                 ^
Co-located client (same host) ---localhost, Bearer token--------+
Browser: GET /?token=...  ---> mini HTML dashboard --------------+
```

The firewall (UFW) exposes only ports 22, 80, and 443. Port 8765 is never
reachable from the internet.

## Components

- `server.py` - FastMCP server: 11 MCP tools plus plain HTTP routes (`/`,
  `/overview`, `/status/{p}`, `/health`).
- `hooks/argos-session-start.sh` - Claude Code SessionStart hook; on session
  start it injects the central status into the context. Project name: the
  `.mc-name` file at the repo root, or the folder name if absent.
- `deploy/argos.service` - systemd unit (installed at
  `/etc/systemd/system/argos.service`).
- `deploy/Caddyfile` - example reverse-proxy config with TLS and security
  headers.
- `deploy/local-client-example.yaml` - example config for a second, co-located
  MCP client connecting over localhost.
- `ARCHITECTURE.md` - components, data model, auth model, and request lifecycle.

## Install / Quick start

These steps assume a Linux host with systemd and Caddy. `/opt/argos` is used as
an example install path.

1. Create a dedicated service user:

   ```
   sudo useradd --system --home /opt/argos --shell /usr/sbin/nologin argos
   sudo mkdir -p /opt/argos && sudo chown argos:argos /opt/argos
   ```

2. Copy the repo into `/opt/argos`, then create a venv and install dependencies:

   ```
   cd /opt/argos
   python3 -m venv venv
   ./venv/bin/pip install -r requirements.txt
   ```

3. Create the environment file and set a strong token:

   ```
   cp .env.example .env
   # edit .env: set MC_TOKEN to the output of: openssl rand -hex 24
   chmod 600 .env
   ```

4. Install and start the systemd unit:

   ```
   sudo cp deploy/argos.service /etc/systemd/system/argos.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now argos
   ```

5. Put it behind Caddy (see `deploy/Caddyfile`), pointing your domain at
   `127.0.0.1:8765`. Caddy obtains a Let's Encrypt certificate automatically.

6. Open only the ports you need in the firewall:

   ```
   sudo ufw allow 22,80,443/tcp
   ```

   Do not expose port 8765.

7. Nightly backups (example cron entry), keeping a 7-day day-of-week rotation:

   ```
   0 3 * * *  argos  sqlite3 /opt/argos/argos.db ".backup /opt/argos/backups/argos-$(date +\%u).db"
   ```

## Client integration

- Register the MCP client (for example, Claude Code on a laptop):

  ```
  claude mcp add --transport http argos https://argos.example.com/mcp \
    --header "Authorization: Bearer <token>"
  ```

- Register the SessionStart hook so a new session auto-injects central status.
  Point the hook at your deployment by setting `MC_URL` and `MC_TOKEN` (for
  example in `~/.claude/argos.env`), then wire `hooks/argos-session-start.sh`
  into your Claude Code SessionStart hooks.

- A second MCP client co-located on the same host can connect over localhost;
  see `deploy/local-client-example.yaml`. Keep a single writer for
  `project_status_set` (for example, Claude Code) so the status note is not
  overwritten by two clients at once.

## License

Licensed under the Apache License, Version 2.0. Copyright 2026 atahanyild.
See the `LICENSE` file for the full text.
