import pytest

import argos_mcp.server as srv


@pytest.fixture
def srvdb(tmp_path, monkeypatch):
    """Point the server at an isolated temp DB and initialize the schema."""
    db_file = tmp_path / "argos-test.db"
    monkeypatch.setattr(srv, "DB_PATH", str(db_file))
    srv.init_db()
    return db_file
