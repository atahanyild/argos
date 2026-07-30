import json

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


def test_job_get_returns_row(srvdb):
    jid = srv.job_enqueue_impl("argos", "T", "S")
    job = srv.job_get_impl(jid)
    assert job["id"] == jid and job["project"] == "argos" and job["state"] == "queued"


def test_job_get_missing_returns_none(srvdb):
    assert srv.job_get_impl(999) is None


def test_job_list_filters(srvdb):
    a = srv.job_enqueue_impl("argos", "A", "S")
    srv.job_enqueue_impl("other", "B", "S")
    rows = srv.job_list_impl(project="argos")
    assert [r["id"] for r in rows] == [a]
    assert set(rows[0].keys()) == {"id", "project", "title", "state", "pr_url", "updated_at"}
