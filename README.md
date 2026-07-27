# Argos

**Argos Panoptes** - the all-seeing memory for your AI agents.

Central project memory: status notes, tasks, decisions, and an activity feed for
all of your projects, collected in a single MCP server backed by one SQLite
database. Multiple AI-agent clients (for example, Claude Code on a laptop and a
second co-located agent on the server) read from and write to the same memory.

Argos serves MCP over Streamable HTTP with a single Bearer token, and can also
run over stdio. Bind it to `127.0.0.1` and reach it however suits you: a reverse
proxy with TLS (Caddy), an SSH tunnel, or stdio over SSH.

```
Laptop (Claude Code, N projects) ---HTTPS + Bearer token---> Caddy (:443, TLS)
                                                                 |
                                                                 v  reverse_proxy
                                                       Argos (127.0.0.1:8765) ---> SQLite (WAL + FTS5)
                                                                 ^
Co-located client (same host) ---localhost, Bearer token--------+
No domain? SSH tunnel or `ssh host argos stdio` ----------------+
Browser: GET /?token=...  ---> mini HTML dashboard -------------+
```

When exposed to the internet, put Argos behind the reverse proxy and let the
firewall expose only ports 22, 80, and 443 - port 8765 should never be reachable
directly from the internet.

## Components

- `argos_mcp/` - the Python package: the FastMCP server (11 MCP tools plus plain
  HTTP routes `/`, `/overview`, `/status/{p}`, `/health`) and the `argos` CLI
  (`argos serve` for HTTP, `argos stdio` for stdio).
- `hooks/argos-session-start.sh` - Claude Code SessionStart hook; on session
  start it injects the central status into the context. Project name: the
  `.mc-name` file at the repo root, or the folder name if absent.
- `Dockerfile`, `docker-compose.yml` - container image and one-command self-host
  stack (with an optional Caddy TLS profile).
- `deploy/argos.service` - systemd unit for a from-source install.
- `deploy/Caddyfile` - reverse-proxy example for a host install (proxies to
  `127.0.0.1:8765`). `deploy/Caddyfile.docker` - the equivalent for the Compose
  `tls` profile (proxies to the `argos` service).
- `deploy/local-client-example.yaml` - example config for a second, co-located
  MCP client connecting over localhost.
- `ARCHITECTURE.md` - components, data model, auth model, and request lifecycle.

## Install / Quick start

Every path reads configuration from the environment (see `.env.example`):
`MC_TOKEN`, `MC_HOST`, `MC_PORT`, `MC_DB`, `MC_STALE_DAYS`.

### Option A - Docker Compose (recommended)

```
git clone https://github.com/atahanyild/argos.git && cd argos
cp .env.example .env
# edit .env: set MC_TOKEN to the output of: openssl rand -hex 24

# Argos only, published to 127.0.0.1:8765 (reach it via an SSH tunnel or your own proxy):
docker compose up -d

# ...or with automatic HTTPS via Caddy (edit deploy/Caddyfile.docker with your domain first):
docker compose --profile tls up -d
```

The SQLite database lives in the `argos-data` volume.

### Option B - pip / pipx

```
pipx install .                 # or: pip install .
export MC_TOKEN=$(openssl rand -hex 24)
argos serve                    # HTTP MCP server on 127.0.0.1:8765
```

### Option C - from source with systemd

Assumes a Linux host with systemd. `/opt/argos` is used as an example path.

```
sudo useradd --system --home /opt/argos --shell /usr/sbin/nologin argos
sudo mkdir -p /opt/argos && sudo chown argos:argos /opt/argos
sudo -u argos git clone https://github.com/atahanyild/argos.git /opt/argos
cd /opt/argos
sudo -u argos python3 -m venv venv
sudo -u argos ./venv/bin/pip install .
sudo -u argos cp .env.example .env      # then edit .env, set MC_TOKEN, chmod 600
sudo cp deploy/argos.service /etc/systemd/system/argos.service
sudo systemctl daemon-reload && sudo systemctl enable --now argos
```

Put it behind Caddy (see `deploy/Caddyfile`) pointing your domain at
`127.0.0.1:8765`; Caddy obtains a Let's Encrypt certificate automatically. Open
only the ports you need, and never expose 8765:

```
sudo ufw allow 22,80,443/tcp
```

Nightly backups (example cron, 7-day day-of-week rotation):

```
0 3 * * *  argos  sqlite3 /opt/argos/argos.db ".backup /opt/argos/backups/argos-$(date +\%u).db"
```

### Connecting without a domain (SSH)

No domain or TLS certificate? SSH already provides encryption and
authentication, so you do not need to expose Argos publicly:

- Port forward (no server change). Keep Argos on `127.0.0.1:8765` and tunnel it:

  ```
  ssh -N -L 8765:localhost:8765 user@your-server
  # then point the MCP client at http://localhost:8765/mcp
  ```

- stdio over SSH (no open port at all). The SSH key is the authentication:

  ```
  # the MCP client runs this as its server command:
  ssh user@your-server argos stdio
  ```

## Client integration

- Register the MCP client over HTTP (for example, Claude Code on a laptop). This
  works the same whether `<url>` is your public domain or `http://localhost:8765`
  through an SSH tunnel:

  ```
  claude mcp add --transport http argos https://argos.example.com/mcp \
    --header "Authorization: Bearer <token>"
  ```

  Or connect over stdio with no open port (see the SSH section):

  ```
  claude mcp add argos -- ssh user@your-server argos stdio
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
