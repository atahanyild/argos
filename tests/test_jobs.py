import argos_mcp.server as srv


def test_jobs_table_exists(srvdb):
    with srv.closing(srv.db()) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {
        "id", "project", "title", "scope", "state", "spec", "question",
        "answer_log", "pr_url", "worker", "approved", "source",
        "created_at", "updated_at",
    } <= cols


def test_job_enqueue_creates_queued_job(srvdb):
    jid = srv.job_enqueue_impl("argos", "Add rate limiting", "Add a token-bucket limiter to /mcp", source="hermes")
    assert isinstance(jid, int) and jid > 0
    with srv.closing(srv.db()) as conn:
        row = conn.execute("SELECT project, title, state, source FROM jobs WHERE id=?", (jid,)).fetchone()
    assert (row["project"], row["title"], row["state"], row["source"]) == ("argos", "Add rate limiting", "queued", "hermes")


def test_job_enqueue_logs_activity(srvdb):
    srv.job_enqueue_impl("argos", "T", "S", source="hermes")
    with srv.closing(srv.db()) as conn:
        n = conn.execute("SELECT count(*) FROM activity WHERE action='job_enqueue'").fetchone()[0]
    assert n == 1
