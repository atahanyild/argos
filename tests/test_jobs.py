import argos_mcp.server as srv


def test_jobs_table_exists(srvdb):
    with srv.closing(srv.db()) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert {
        "id", "project", "title", "scope", "state", "spec", "question",
        "answer_log", "pr_url", "worker", "approved", "source",
        "created_at", "updated_at",
    } <= cols
