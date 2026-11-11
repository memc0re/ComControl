"""Login, sessions, CSRF, origin checks, password change and persistence."""

import hashlib

from fastapi.testclient import TestClient

from comcontrol.main import build

from .conftest import make_env, test_settings


def test_default_admin_created_and_login(env):
    info = env.login()
    assert info["username"] == "admin" and info["password_is_default"] is True
    assert env.client.get("/api/v1/auth/session").status_code == 200


def test_session_cookie_attributes(env):
    r = env.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert "secure" not in cookie.replace("samesite=strict", "")
    assert "max-age=28800" in cookie


def test_only_token_hash_stored(env):
    env.login()
    token = env.client.cookies.get("comcontrol_session")
    rows = env.rt.db.query("SELECT token_hash FROM sessions")
    assert [r["token_hash"] for r in rows] == [hashlib.sha256(token.encode()).hexdigest()]


def test_wrong_credentials(env):
    for body in ({"username": "admin", "password": "nope"}, {"username": "root", "password": "admin"}):
        r = env.client.post("/api/v1/auth/login", json=body)
        assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_credentials"


def test_access_without_session(env):
    for path in ("/api/v1/state", "/api/v1/config", "/api/v1/device", "/api/v1/auth/session"):
        r = env.client.get(path)
        assert r.status_code == 401 and r.json()["error"]["code"] == "not_authenticated"
    assert env.client.get("/api/v1/health").status_code == 200  # health is public


def test_write_requires_csrf_token(env):
    env.login()
    body = {"revision": env.revision(), "usage": "expected", "name": "x"}
    r = env.client.put("/api/v1/sensors/temp1", json=body)
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_invalid"
    r = env.client.put("/api/v1/sensors/temp1", json=body, headers={"X-CSRF-Token": "wrong"})
    assert r.status_code == 403
    assert env.client.put("/api/v1/sensors/temp1", json=body, headers=env.h).status_code == 200


def test_cross_origin_rejected(env):
    r = env.client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "origin_not_allowed"
    r = env.client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r.status_code == 403
    r = env.client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"},
        headers={"Origin": "http://testserver"},
    )
    assert r.status_code == 200


def test_configured_dev_origin_allowed(tmp_path):
    gen = make_env(tmp_path, allowed_origins=["http://localhost:5173"])
    env = next(gen)
    r = env.client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "admin"},
        headers={"Origin": "http://localhost:5173"},
    )
    assert r.status_code == 200
    next(gen, None)


def test_logout_invalidates_session(env):
    env.login()
    old = env.client.cookies.get("comcontrol_session")
    assert env.client.post("/api/v1/auth/logout", headers=env.h).status_code == 204
    env.client.cookies.set("comcontrol_session", old)
    assert env.client.get("/api/v1/state").status_code == 401


def test_session_expires_after_8_hours(env):
    auth = env.client.app.state.auth
    now = [1_000_000.0]
    auth.clock = lambda: now[0]
    env.login()
    now[0] += 8 * 3600 - 1
    assert env.client.get("/api/v1/state").status_code == 200
    now[0] += 2
    assert env.client.get("/api/v1/state").status_code == 401


def test_login_rate_limited(env):
    for _ in range(5):
        env.client.post("/api/v1/auth/login", json={"username": "admin", "password": "bad"})
    r = env.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "login_rate_limited"
    assert int(r.headers["Retry-After"]) > 0


def test_forwarded_header_not_trusted_for_rate_limit(env):
    for i in range(5):
        env.client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "bad"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
    r = env.client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"},
                        headers={"X-Forwarded-For": "10.0.0.99"})
    assert r.status_code == 429


def test_password_change_rules(env):
    env.login()

    def change(cur, new):
        return env.client.post("/api/v1/auth/password", headers=env.h,
                               json={"current_password": cur, "new_password": new})

    assert change("wrong", "longenough1").json()["error"]["code"] == "current_password_wrong"
    assert change("admin", "short").json()["error"]["code"] == "password_too_short"
    assert change("admin", "admin").status_code == 400
    assert change("admin", "new-secret-pw").status_code == 204
    assert env.client.get("/api/v1/auth/session").json()["password_is_default"] is False


def test_password_change_ends_other_sessions(env):
    env.login()
    other = TestClient(env.client.app)
    assert other.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"}).status_code == 200
    r = env.client.post("/api/v1/auth/password", headers=env.h,
                        json={"current_password": "admin", "new_password": "new-secret-pw"})
    assert r.status_code == 204
    assert other.get("/api/v1/state").status_code == 401
    assert env.client.get("/api/v1/state").status_code == 200


def test_changed_password_survives_restart(tmp_path):
    gen = make_env(tmp_path)
    env = next(gen)
    env.login()
    env.client.post("/api/v1/auth/password", headers=env.h,
                    json={"current_password": "admin", "new_password": "persisted-pw"})
    next(gen, None)

    # restart (same data directory) - default account must not come back
    app, rt, db = build(test_settings(tmp_path / "data"))
    with TestClient(app) as client:
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        assert r.status_code == 401
        r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "persisted-pw"})
        assert r.status_code == 200 and r.json()["password_is_default"] is False
    db.close()


def test_password_hash_is_argon2id(env):
    row = env.rt.db.one("SELECT password_hash FROM admin")
    assert row["password_hash"].startswith("$argon2id$")
