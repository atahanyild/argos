import argos_mcp.server as srv


def test_init_creates_tables(srvdb):
    with srv.closing(srv.db()) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
    assert {"projects", "tasks", "decisions", "activity"} <= names
