import argos_mcp.server as srv


def test_jobs_html_escapes_and_shows_state(srvdb):
    srv.job_enqueue_impl("argos", "Add <b>limiter</b>", "S")
    html_fragment = srv._jobs_html(srv.job_list_impl())
    assert "Add &lt;b&gt;limiter&lt;/b&gt;" in html_fragment  # escaped, not raw
    assert "queued" in html_fragment
    assert "argos" in html_fragment


def test_jobs_html_empty(srvdb):
    assert "No jobs" in srv._jobs_html([])


def test_jobs_html_shows_pr_link(srvdb):
    jid = srv.job_enqueue_impl("argos", "T", "S")
    srv.job_update_impl(jid, state="pr_opened", pr_url="https://github.com/x/y/pull/1")
    frag = srv._jobs_html(srv.job_list_impl())
    assert 'href="https://github.com/x/y/pull/1"' in frag
