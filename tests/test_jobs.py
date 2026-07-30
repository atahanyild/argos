import json

import pytest

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


def test_job_claim_takes_oldest_and_sets_planning(srvdb):
    first = srv.job_enqueue_impl("argos", "first", "S")
    srv.job_enqueue_impl("argos", "second", "S")
    claimed = srv.job_claim_impl("runner-1")
    assert claimed["id"] == first
    assert claimed["state"] == "planning"
    assert claimed["worker"] == "runner-1"


def test_job_claim_is_atomic_no_double_claim(srvdb):
    srv.job_enqueue_impl("argos", "only", "S")
    a = srv.job_claim_impl("runner-1")
    b = srv.job_claim_impl("runner-2")
    assert a is not None and b is None


def test_job_claim_none_when_empty(srvdb):
    assert srv.job_claim_impl("runner-1") is None


def test_job_update_sets_state_and_spec(srvdb):
    jid = srv.job_enqueue_impl("argos", "T", "S")
    assert srv.job_update_impl(jid, state="awaiting_approval", spec="## Spec\ncriteria") is True
    job = srv.job_get_impl(jid)
    assert job["state"] == "awaiting_approval" and job["spec"] == "## Spec\ncriteria"


def test_job_update_question_clear_sentinel(srvdb):
    jid = srv.job_enqueue_impl("argos", "T", "S")
    srv.job_update_impl(jid, state="clarifying", question="Which database?")
    assert srv.job_get_impl(jid)["question"] == "Which database?"
    srv.job_update_impl(jid, state="planning", question="-")
    assert srv.job_get_impl(jid)["question"] == ""


def test_job_update_rejects_unknown_state(srvdb):
    jid = srv.job_enqueue_impl("argos", "T", "S")
    with pytest.raises(ValueError):
        srv.job_update_impl(jid, state="bogus")


def test_job_update_missing_job_returns_false(srvdb):
    assert srv.job_update_impl(999, state="executing") is False
