"""
Argos MCP Server
================
A central brain that holds the status, tasks, and decisions of all projects.
Local Claude Code sessions and a co-located MCP client on the host connect to
the same server.

Run (via the ``argos`` console script or ``python -m argos_mcp``):
    argos serve             # HTTP transport (default)
    argos stdio             # stdio transport (for SSH / local MCP clients)

Configuration comes from the environment (see .env.example):
    MC_TOKEN, MC_HOST, MC_PORT, MC_DB, MC_STALE_DAYS

MCP endpoint:  http://HOST:8765/mcp   (Streamable HTTP, Bearer token)
Plain HTTP:    GET /            -> mini HTML dashboard (?token=...)
               GET /overview    -> plain-text summary (for hooks)
               GET /status/{p}  -> plain-text status of a single project
               GET /health      -> health check (no token required)
"""

import html
import json
import os
import sqlite3
import time
from contextlib import closing

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse

DB_PATH = os.environ.get("MC_DB", "argos.db")  # relative to the working directory
TOKEN = os.environ.get("MC_TOKEN", "")  # if empty, auth is disabled (localhost only!)
STALE_DAYS = int(os.environ.get("MC_STALE_DAYS", "7"))
JOB_STATES = (
    "queued", "planning", "clarifying", "awaiting_approval",
    "executing", "needs_input", "pr_opened", "failed", "cancelled",
)

auth = (
    StaticTokenVerifier(tokens={TOKEN: {"client_id": "trusted", "scopes": []}})
    if TOKEN
    else None
)

mcp = FastMCP(
    "argos",
    auth=auth,
    instructions=(
        "Central project memory. At the start of a session, read the state with "
        "projects_overview / project_status_get; write it back with project_status_set, "
        "task_*, and decision_log on important decisions, milestones, and at session end. "
        "On write tools, set the source parameter to who you are (e.g. claude-code)."
    ),
)

# ---------------------------------------------------------------- DB helpers

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects(
                name        TEXT PRIMARY KEY,
                description TEXT DEFAULT '',
                status_md   TEXT DEFAULT '',
                repo_path   TEXT DEFAULT '',
                archived    INTEGER DEFAULT 0,
                updated_at  REAL
            );
            CREATE TABLE IF NOT EXISTS tasks(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL,
                title      TEXT NOT NULL,
                status     TEXT DEFAULT 'open',      -- open | in_progress | blocked | done
                priority   INTEGER DEFAULT 3,        -- 1 (high) .. 5 (low)
                notes      TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL
            );
            CREATE TABLE IF NOT EXISTS decisions(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL,
                decision   TEXT NOT NULL,
                rationale  TEXT DEFAULT '',
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS activity(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL,
                source     TEXT DEFAULT '',
                action     TEXT NOT NULL,
                detail     TEXT DEFAULT '',
                created_at REAL
            );
            CREATE TABLE IF NOT EXISTS jobs(
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                project    TEXT NOT NULL,
                title      TEXT NOT NULL,
                scope      TEXT NOT NULL,
                state      TEXT NOT NULL DEFAULT 'queued',
                spec       TEXT DEFAULT '',
                question   TEXT DEFAULT '',
                answer_log TEXT DEFAULT '',
                pr_url     TEXT DEFAULT '',
                worker     TEXT DEFAULT '',
                approved   INTEGER DEFAULT 0,
                source     TEXT DEFAULT '',
                created_at REAL,
                updated_at REAL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS search_idx USING fts5(
                project, kind, ref_id, content
            );
            """
        )


def now() -> float:
    return time.time()


def ts(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def index_row(conn, project: str, kind: str, ref_id: str, content: str):
    conn.execute("DELETE FROM search_idx WHERE kind=? AND ref_id=?", (kind, ref_id))
    conn.execute(
        "INSERT INTO search_idx(project, kind, ref_id, content) VALUES(?,?,?,?)",
        (project, kind, ref_id, content),
    )


def log_activity(conn, project: str, source: str, action: str, detail: str = ""):
    conn.execute(
        "INSERT INTO activity(project, source, action, detail, created_at) VALUES(?,?,?,?,?)",
        (project, source or "?", action, detail[:300], now()),
    )


def touch(conn, project: str):
    conn.execute("UPDATE projects SET updated_at=? WHERE name=?", (now(), project))


# ------------------------------------------------------------ implementations
# Tool bodies live here as plain functions; both the MCP tools and the plain
# HTTP routes call them. (The @mcp.tool decorator turns a function into a
# FunctionTool object, so the routes cannot call it directly.)

def overview_impl(include_archived: bool = False) -> str:
    q = """
        SELECT p.name, p.description, p.updated_at, p.archived,
               SUM(CASE WHEN t.status IN ('open','in_progress') THEN 1 ELSE 0 END) AS open_n,
               SUM(CASE WHEN t.status='blocked' THEN 1 ELSE 0 END) AS blocked_n
        FROM projects p LEFT JOIN tasks t ON t.project = p.name
        """
    if not include_archived:
        q += " WHERE p.archived=0"
    q += " GROUP BY p.name ORDER BY p.updated_at DESC"
    with closing(db()) as conn:
        rows = conn.execute(q).fetchall()
    if not rows:
        return "No projects registered. Add one with project_upsert."
    lines = []
    for r in rows:
        stale = " [STALE]" if r["updated_at"] and (now() - r["updated_at"]) > STALE_DAYS * 86400 else ""
        arch = " [ARCHIVED]" if r["archived"] else ""
        lines.append(
            f"- {r['name']}{arch}{stale}: {r['description'] or '(no description)'} | "
            f"open: {r['open_n'] or 0}, blocked: {r['blocked_n'] or 0} | "
            f"last update: {ts(r['updated_at'])}"
        )
    return "\n".join(lines)


def status_impl(name: str) -> str:
    with closing(db()) as conn:
        p = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
        if not p:
            return f"Project '{name}' not found. Call projects_overview to list known projects."
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE project=? AND status!='done' "
            "ORDER BY priority, updated_at DESC", (name,),
        ).fetchall()
        decisions = conn.execute(
            "SELECT * FROM decisions WHERE project=? ORDER BY created_at DESC LIMIT 5", (name,),
        ).fetchall()
        acts = conn.execute(
            "SELECT * FROM activity WHERE project=? ORDER BY created_at DESC LIMIT 5", (name,),
        ).fetchall()

    out = [f"# {p['name']}", p["description"] or "", ""]
    out += [f"## Status note (last update: {ts(p['updated_at'])})",
            p["status_md"] or "(no status note yet)", ""]
    out.append("## Open tasks")
    if tasks:
        for t in tasks:
            out.append(f"- [#{t['id']}] ({t['status']}, P{t['priority']}) {t['title']}"
                       + (f" — {t['notes']}" if t["notes"] else ""))
    else:
        out.append("(no open tasks)")
    out += ["", "## Recent decisions"]
    if decisions:
        for d in decisions:
            out.append(f"- [{ts(d['created_at'])}] {d['decision']}"
                       + (f" — {d['rationale']}" if d["rationale"] else ""))
    else:
        out.append("(no decisions recorded)")
    out += ["", "## Recent activity"]
    if acts:
        for a in acts:
            out.append(f"- [{ts(a['created_at'])}] {a['source']}: {a['action']}"
                       + (f" — {a['detail']}" if a["detail"] else ""))
    else:
        out.append("(no activity)")
    return "\n".join(out)


def job_enqueue_impl(project: str, title: str, scope: str, source: str = "") -> int:
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO jobs(project, title, scope, state, source, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (project, title, scope, "queued", source, now(), now()),
        )
        jid = cur.lastrowid
        log_activity(conn, project, source, "job_enqueue", f"#{jid} {title}")
    return jid


def job_get_impl(job_id: int) -> dict | None:
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def job_claim_impl(worker: str) -> dict | None:
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "UPDATE jobs SET state='planning', worker=?, updated_at=? "
            "WHERE id = (SELECT id FROM jobs WHERE state='queued' "
            "            ORDER BY created_at LIMIT 1) AND state='queued'",
            (worker, now()),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT id FROM jobs WHERE worker=? AND state='planning' "
            "ORDER BY updated_at DESC LIMIT 1", (worker,),
        ).fetchone()
        jid = row["id"]
        log_activity(conn, "", worker, "job_claim", f"#{jid}")
    return job_get_impl(jid)


def job_list_impl(state: str = "", project: str = "") -> list[dict]:
    q = "SELECT id, project, title, state, pr_url, updated_at FROM jobs WHERE 1=1"
    args: list = []
    if state:
        q += " AND state=?"
        args.append(state)
    if project:
        q += " AND project=?"
        args.append(project)
    q += " ORDER BY updated_at DESC"
    with closing(db()) as conn:
        return [dict(r) for r in conn.execute(q, args).fetchall()]


# -------------------------------------------------------------------- tools

@mcp.tool
def projects_overview(include_archived: bool = False) -> str:
    """Summary of all active projects: open/blocked task counts, last update.
    Projects untouched for 7+ days are marked [STALE]. Call once at session start."""
    return overview_impl(include_archived)


@mcp.tool
def project_upsert(name: str, description: str = "", repo_path: str = "", source: str = "") -> str:
    """Register a new project or update its description/repo path."""
    with closing(db()) as conn, conn:
        conn.execute(
            """INSERT INTO projects(name, description, repo_path, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 description=CASE WHEN excluded.description!='' THEN excluded.description ELSE projects.description END,
                 repo_path=CASE WHEN excluded.repo_path!='' THEN excluded.repo_path ELSE projects.repo_path END,
                 archived=0,
                 updated_at=excluded.updated_at""",
            (name, description, repo_path, now()),
        )
        log_activity(conn, name, source, "project_upsert", description)
    return f"Project '{name}' saved."


@mcp.tool
def project_status_get(name: str) -> str:
    """Full status of a project: status note (markdown), open tasks, last 5
    decisions, last 5 activity entries. Call when you start working on a project."""
    return status_impl(name)


@mcp.tool
def job_enqueue(project: str, title: str, scope: str, source: str = "") -> str:
    """Queue a delegated coding job for a project. title is a short label; scope
    describes what to do. The job starts in the 'queued' state for a runner to claim."""
    jid = job_enqueue_impl(project, title, scope, source)
    return f"Job #{jid} queued for {project}."


@mcp.tool
def job_get(job_id: int) -> str:
    """Full JSON of a single job, or a not-found message."""
    job = job_get_impl(job_id)
    return json.dumps(job) if job else f"Job #{job_id} not found."


@mcp.tool
def job_list(state: str = "", project: str = "") -> str:
    """JSON list of jobs (id, project, title, state, pr_url, updated_at), newest
    first. Filter by state and/or project; empty means all."""
    return json.dumps(job_list_impl(state, project))


@mcp.tool
def job_claim(worker: str) -> str:
    """Atomically claim the oldest queued job for this worker, moving it to
    'planning'. Returns the claimed job as JSON, or a message if none are queued."""
    job = job_claim_impl(worker)
    return json.dumps(job) if job else "No queued jobs."


@mcp.tool
def project_status_set(name: str, status_md: str, source: str = "") -> str:
    """Replace the project's status note (markdown) ENTIRELY. Call at session
    end, at a milestone, or when context changes. Write it distilled: what is
    done, what is in progress, the next step, and known risks."""
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "UPDATE projects SET status_md=?, updated_at=? WHERE name=?",
            (status_md, now(), name),
        )
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO projects(name, status_md, updated_at) VALUES(?,?,?)",
                (name, status_md, now()),
            )
        index_row(conn, name, "status", name, status_md)
        log_activity(conn, name, source, "status_set", status_md[:120])
    return f"Status note for '{name}' updated."


@mcp.tool
def project_archive(name: str, source: str = "") -> str:
    """Archive the project (hidden in overview). project_upsert reactivates it."""
    with closing(db()) as conn, conn:
        cur = conn.execute("UPDATE projects SET archived=1 WHERE name=?", (name,))
        if cur.rowcount == 0:
            return f"Project '{name}' not found."
        log_activity(conn, name, source, "archive")
    return f"Project '{name}' archived."


@mcp.tool
def task_add(project: str, title: str, priority: int = 3, notes: str = "", source: str = "") -> str:
    """Add a new task to the project. priority: 1 (urgent) .. 5 (low)."""
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO tasks(project, title, priority, notes, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (project, title, priority, notes, now(), now()),
        )
        tid = cur.lastrowid
        index_row(conn, project, "task", str(tid), f"{title} {notes}")
        log_activity(conn, project, source, "task_add", f"#{tid} {title}")
        touch(conn, project)
    return f"Task #{tid} added: {title}"


@mcp.tool
def task_update(task_id: int, status: str = "", notes: str = "", source: str = "") -> str:
    """Update a task. status: open | in_progress | blocked | done.
    If notes is given, it overwrites the existing notes."""
    with closing(db()) as conn, conn:
        t = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            return f"Task #{task_id} not found."
        new_status = status or t["status"]
        new_notes = notes if notes else t["notes"]
        conn.execute(
            "UPDATE tasks SET status=?, notes=?, updated_at=? WHERE id=?",
            (new_status, new_notes, now(), task_id),
        )
        index_row(conn, t["project"], "task", str(task_id), f"{t['title']} {new_notes}")
        log_activity(conn, t["project"], source, "task_update", f"#{task_id} -> {new_status}")
        touch(conn, t["project"])
    return f"Task #{task_id} -> {new_status}"


@mcp.tool
def task_list(project: str = "", status: str = "active") -> str:
    """List tasks. If project is empty, list all projects. status: active
    (not done) | open | in_progress | blocked | done | all."""
    q = "SELECT * FROM tasks WHERE 1=1"
    args: list = []
    if project:
        q += " AND project=?"
        args.append(project)
    if status == "active":
        q += " AND status!='done'"
    elif status != "all":
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY project, priority, updated_at DESC"
    with closing(db()) as conn:
        rows = conn.execute(q, args).fetchall()
    if not rows:
        return "No matching tasks."
    return "\n".join(
        f"- [#{r['id']}] {r['project']} | ({r['status']}, P{r['priority']}) {r['title']}"
        + (f" — {r['notes']}" if r["notes"] else "")
        for r in rows
    )


@mcp.tool
def decision_log(project: str, decision: str, rationale: str = "", source: str = "") -> str:
    """Record a durable technical/product decision (e.g. 'use sessions, not JWT,
    for auth'). Write every decision that future sessions and other clients
    should see."""
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO decisions(project, decision, rationale, created_at) VALUES(?,?,?,?)",
            (project, decision, rationale, now()),
        )
        index_row(conn, project, "decision", str(cur.lastrowid), f"{decision} {rationale}")
        log_activity(conn, project, source, "decision", decision[:120])
        touch(conn, project)
    return "Decision recorded."


@mcp.tool
def recent_activity(project: str = "", hours: int = 48) -> str:
    """All activity in the last N hours (who did what). For questions like
    'what happened yesterday?'. If project is empty, list all projects."""
    q = "SELECT * FROM activity WHERE created_at > ?"
    args: list = [now() - hours * 3600]
    if project:
        q += " AND project=?"
        args.append(project)
    q += " ORDER BY created_at DESC LIMIT 100"
    with closing(db()) as conn:
        rows = conn.execute(q, args).fetchall()
    if not rows:
        return f"No activity in the last {hours} hours."
    return "\n".join(
        f"- [{ts(r['created_at'])}] {r['project']} / {r['source']}: {r['action']}"
        + (f" — {r['detail']}" if r["detail"] else "")
        for r in rows
    )


@mcp.tool
def search(query: str, project: str = "") -> str:
    """Full-text search across all status notes, tasks, and decisions (FTS5)."""
    q = ("SELECT project, kind, ref_id, snippet(search_idx, 3, '[', ']', '…', 20) AS snip "
         "FROM search_idx WHERE search_idx MATCH ?")
    args = [query]
    if project:
        q += " AND project=?"
        args.append(project)
    q += " LIMIT 25"
    with closing(db()) as conn:
        try:
            rows = conn.execute(q, args).fetchall()
        except sqlite3.OperationalError as e:
            return f"Search error: {e}"
    if not rows:
        return "No results."
    return "\n".join(f"- {r['project']} / {r['kind']}#{r['ref_id']}: {r['snip']}" for r in rows)


# -------------------------------------------- plain HTTP endpoints + dashboard

def _authorized(request: Request) -> bool:
    if not TOKEN:
        return True
    if request.headers.get("authorization") == f"Bearer {TOKEN}":
        return True
    return request.query_params.get("token") == TOKEN


@mcp.custom_route("/health", methods=["GET"])
async def health(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


@mcp.custom_route("/overview", methods=["GET"])
async def overview_route(request: Request) -> PlainTextResponse:
    if not _authorized(request):
        return PlainTextResponse("unauthorized", status_code=401)
    return PlainTextResponse(overview_impl())


@mcp.custom_route("/status/{name}", methods=["GET"])
async def status_route(request: Request) -> PlainTextResponse:
    if not _authorized(request):
        return PlainTextResponse("unauthorized", status_code=401)
    name = request.path_params["name"]
    with closing(db()) as conn:
        exists = conn.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone()
    # Signal a missing project with 404 so simple clients (curl -f) can rely on
    # the status code instead of matching text in the body.
    return PlainTextResponse(status_impl(name), status_code=200 if exists else 404)


@mcp.custom_route("/", methods=["GET"])
async def dashboard(request: Request) -> HTMLResponse:
    if not _authorized(request):
        return HTMLResponse("<h3>unauthorized — add ?token=...</h3>", status_code=401)
    with closing(db()) as conn:
        projects = conn.execute(
            "SELECT * FROM projects WHERE archived=0 ORDER BY updated_at DESC"
        ).fetchall()
        tasks = conn.execute(
            "SELECT * FROM tasks WHERE status!='done' ORDER BY project, priority"
        ).fetchall()
        acts = conn.execute(
            "SELECT * FROM activity ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    e = html.escape
    rows = []
    for p in projects:
        stale = (now() - (p["updated_at"] or 0)) > STALE_DAYS * 86400
        ptasks = [t for t in tasks if t["project"] == p["name"]]
        badge = " <span style='color:#c00'>[STALE]</span>" if stale else ""
        tlist = "".join(
            f"<li>#{t['id']} <b>{e(t['title'])}</b> <small>({t['status']}, P{t['priority']})</small></li>"
            for t in ptasks
        ) or "<li><i>no open tasks</i></li>"
        rows.append(
            f"<div class='card'><h2>{e(p['name'])}{badge}</h2>"
            f"<p><small>{e(p['description'] or '')} — son: {ts(p['updated_at'])}</small></p>"
            f"<pre>{e(p['status_md'] or '(no status note)')}</pre><ul>{tlist}</ul></div>"
        )
    feed = "".join(
        f"<li>[{ts(a['created_at'])}] <b>{e(a['project'])}</b> / {e(a['source'])}: "
        f"{e(a['action'])} {e(a['detail'] or '')}</li>"
        for a in acts
    ) or "<li><i>no activity</i></li>"
    page = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>Argos</title>
<style>
 body{{font-family:system-ui;margin:2rem;background:#fafafa;color:#222}}
 .card{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:1rem;margin-bottom:1rem}}
 pre{{white-space:pre-wrap;background:#f4f4f4;padding:.6rem;border-radius:6px}}
 h1{{margin-top:0}} small{{color:#666}}
</style></head><body>
<h1>Argos</h1>
{''.join(rows) or '<p>No projects.</p>'}
<h2>Recent activity</h2><ul>{feed}</ul>
</body></html>"""
    return HTMLResponse(page)
