#!/usr/bin/env bash
# Claude Code SessionStart hook: on session start, inject the project's central status into the context.
# Project name resolution: if a .mc-name file exists at the repo root, use its contents, otherwise the folder name.
# MC_URL/MC_TOKEN precedence: environment variable > ~/.claude/argos.env > default.
[ -f "$HOME/.claude/argos.env" ] && . "$HOME/.claude/argos.env"
MC_URL="${MC_URL:-http://127.0.0.1:8765}"
MC_TOKEN="${MC_TOKEN:-}"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
if [ -f "$ROOT/.mc-name" ]; then
  PROJECT_NAME="$(head -1 "$ROOT/.mc-name" | tr -d '[:space:]')"
else
  PROJECT_NAME="$(basename "$ROOT")"
fi

ENCODED="$(python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$PROJECT_NAME" 2>/dev/null || echo "$PROJECT_NAME")"

AUTH=()
[ -n "$MC_TOKEN" ] && AUTH=(-H "Authorization: Bearer $MC_TOKEN")
# A missing project returns 404, so `curl -f` fails and STATUS stays empty; we
# then treat it as "no record". No body-substring matching (a status note may
# legitimately contain any text, including the words "not found").
STATUS="$(curl -sf --max-time 5 "${AUTH[@]}" "$MC_URL/status/$ENCODED" 2>/dev/null)"

if [ -n "$STATUS" ]; then
  echo "[Argos] Central status for project '$PROJECT_NAME':"
  echo "$STATUS"
  echo
  echo "(Remember to update this at session end with project_status_set and task_update. Use source=claude-code.)"
else
  echo "[Argos] No central record for '$PROJECT_NAME', or the server is unreachable. Register it with project_upsert if needed."
fi
exit 0
