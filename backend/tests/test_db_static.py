"""Migrations and static/SPA delivery."""

import pytest
from fastapi.testclient import TestClient

from comcontrol import db as db_mod
from comcontrol.db import Database, SchemaTooNewError
from comcontrol.main import build

from .conftest import test_settings


def test_migrations_apply_once(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    assert db.version == 0
    assert db.migrate() == db_mod.SCHEMA_VERSION
    assert db.migrate() == db_mod.SCHEMA_VERSION  # idempotent
    assert len(db.query("SELECT * FROM fans")) == 6
    assert len(db.query("SELECT * FROM sensors")) == 4
    db.close()


def test_newer_schema_refused(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    db.migrate()
    db.query(f"PRAGMA user_version = {db_mod.SCHEMA_VERSION + 1}")
    with pytest.raises(SchemaTooNewError):
        db.migrate()
    db.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    db = Database(tmp_path / "x.sqlite3")
    db.migrate()
    monkeypatch.setattr(db_mod, "MIGRATIONS", [*db_mod.MIGRATIONS, (99, "CREATE TABLE t (a); SELECT nope FROM t")])
    monkeypatch.setattr(db_mod, "SCHEMA_VERSION", 99)
    with pytest.raises(Exception):
        db.migrate()
    assert db.version == db_mod.MIGRATIONS[-2][0]
    assert not db.query("SELECT name FROM sqlite_master WHERE name = 't'")
    db.close()


@pytest.fixture
def static_client(tmp_path):
    static = tmp_path / "static"
    (static / "_app").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>ComControl</title>")
    (static / "_app" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("outside")
    app, rt, db = build(test_settings(tmp_path / "data", static_dir=static))
    with TestClient(app) as client:
        yield client
    db.close()


def test_spa_fallback_only_for_known_routes(static_client):
    for path in ("/", "/channels", "/settings"):
        r = static_client.get(path)
        assert r.status_code == 200 and "ComControl" in r.text
    assert static_client.get("/_app/app.js").status_code == 200
    for path in ("/_app/missing.js", "/unknown-page", "/favicon-nope.png"):
        r = static_client.get(path)
        assert r.status_code == 404 and not r.headers["content-type"].startswith("text/html")


def test_unknown_api_path_never_returns_html(static_client):
    for path in ("/api/v1/nothing", "/api/v2/state", "/api"):
        r = static_client.get(path)
        assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


def test_no_path_traversal(static_client):
    r = static_client.get("/../secret.txt")
    assert r.status_code == 404 or "outside" not in r.text
    r = static_client.get("/_app/..%2F..%2Fsecret.txt")
    assert "outside" not in r.text
