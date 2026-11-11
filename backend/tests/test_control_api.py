"""Live state, channel configuration and control via the API (simulator)."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from comcontrol.live import purge_history
from comcontrol.main import build

from .conftest import make_env, test_settings

CURVE = [{"temperature_c": 20, "rpm": 500}, {"temperature_c": 30, "rpm": 1500}]


def fan(state, fid):
    return next(f for f in state["fans"] if f["id"] == fid)


def sensor(state, sid):
    return next(s for s in state["sensors"] if s["id"] == sid)


def activate(env, fid="fan1", max_rpm=1500):
    r = env.put_fan(fid, usage="expected", name="Front", max_rpm=max_rpm)
    assert r.status_code == 200, r.text
    return r.json()


# --- live state ---------------------------------------------------------------

def test_state_before_first_poll_has_no_values(env):
    env.login()
    s = env.state()
    assert s["simulated"] is True and s["data_age_s"] is None
    t = sensor(s, "temp1")["temperature"]
    assert t["value"] is None and t["status"] == "no_data"


def test_state_after_poll(env):
    env.login()
    env.tick()
    s = env.state()
    assert s["connection"] == "connected"
    t1 = sensor(s, "temp1")
    assert t1["status"] == "unused" and t1["detected"] is True
    assert t1["temperature"]["value"] == 24.0 and t1["temperature"]["unit"] == "°C"
    assert t1["temperature"]["measured_at"].endswith("Z")
    # unconnected sensor / fan: gap, never 0
    t2 = sensor(s, "temp2")["temperature"]
    assert t2["value"] is None and t2["status"] == "not_connected"
    f2 = fan(s, "fan2")
    assert f2["rpm"]["value"] is None and f2["detected_mode"] == "disconnected"
    assert fan(s, "fan1")["rpm"]["value"] == 1500 and fan(s, "fan1")["detected_mode"] == "pwm"
    assert {r["id"] for r in s["rails"]} == {"rail_12v", "rail_5v", "rail_3v3"}


def test_channel_status_expected_present_and_missing(env):
    env.login()
    env.tick()
    activate(env)
    assert env.put_sensor("temp1", usage="expected").status_code == 200
    assert env.put_sensor("temp2", usage="expected").status_code == 200
    env.put_fan("fan2", usage="expected", max_rpm=1000)
    env.tick()
    s = env.state()
    assert sensor(s, "temp1")["status"] == "present"
    assert sensor(s, "temp2")["status"] == "expected_missing"
    assert fan(s, "fan1")["status"] == "present"
    assert fan(s, "fan2")["status"] == "expected_missing"
    assert fan(s, "fan3")["status"] == "unused"


def test_values_become_stale_after_15_s_without_update(env):
    env.login()
    env.put_sensor("temp1", usage="expected")
    env.tick()
    env.clock.advance(15)
    assert sensor(env.state(), "temp1")["temperature"]["status"] == "ok"
    env.sim.faults.unplugged = True
    env.tick()  # poll fails -> nothing refreshed
    env.clock.advance(0.5)
    s = env.state()
    t = sensor(s, "temp1")
    assert s["connection"] == "disconnected" and s["connection_error"] == "device_io_error"
    assert t["status"] == "stale" and t["temperature"]["value"] is None
    assert t["temperature"]["last_value"] == 24.0


def test_read_error_is_a_gap_and_keeps_old_timestamp(env):
    env.login()
    env.tick()
    first = sensor(env.state(), "temp1")["temperature"]["last_valid_at"]
    env.clock.advance(5)
    env.sim.faults.status_error_reads = 1
    env.tick()
    t = sensor(env.state(), "temp1")["temperature"]
    assert t["status"] == "error" and t["value"] is None and t["error"] == "device_status_10"
    assert t["last_valid_at"] == first  # no blanket new timestamp


def test_zero_rpm_of_present_fan_is_a_valid_value(env):
    env.login()
    env.tick()
    activate(env)
    assert env.apply("fan1", control_type="fixed", percent=0).status_code == 200
    env.tick()
    r = fan(env.state(), "fan1")["rpm"]
    assert r["value"] == 0.0 and r["status"] == "ok"


def test_state_does_not_touch_hardware(env):
    env.login()
    env.tick()
    before = len(env.sim.sent)
    for _ in range(5):
        env.state()
    assert len(env.sim.sent) == before


# --- configuration and control ---------------------------------------------------

def test_activation_sends_full_power(env):
    env.login()
    env.tick()
    env.sim.fans[0].percent = 30
    body = activate(env)
    assert body["fan"]["control_type"] == "fixed" and body["fan"]["percent"] == 100
    assert body["outcome"]["ok"] is True
    assert env.sim.fans[0].percent == 100
    last = fan(env.state(), "fan1")["last_command"]
    assert last["ok"] and last["command"]["percent"] == 100 and last["command"]["reason"] == "activation"


def test_apply_fixed_power(env):
    env.login()
    env.tick()
    activate(env)
    r = env.apply("fan1", control_type="fixed", percent=40)
    assert r.status_code == 200, r.text
    assert r.json()["fan"]["percent"] == 40 and r.json()["outcome"]["ok"]
    env.tick()
    assert fan(env.state(), "fan1")["rpm"]["value"] == 600


def test_apply_curve_padded_and_executed(env):
    env.login()
    env.tick()
    activate(env)
    env.put_sensor("temp1", usage="expected")
    r = env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    assert r.status_code == 200, r.text
    f = r.json()["fan"]
    assert len(f["curve_points"]) == 2 and len(f["effective_curve"]) == 6
    assert f["effective_curve"][-1] == {"temperature_c": 30.04, "rpm": 1500}
    env.tick()
    assert fan(env.state(), "fan1")["rpm"]["value"] == 900  # 24 °C on 20:500..30:1500


def test_apply_rejections(env):
    env.login()
    env.tick()
    # not expected yet
    assert env.apply("fan1", control_type="fixed", percent=50).json()["error"]["code"] == "fan_not_expected"
    activate(env)
    # sensor not configured as expected
    r = env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    assert r.json()["error"]["code"] == "curve_sensor_not_expected"
    env.put_sensor("temp1", usage="expected")
    env.put_sensor("temp2", usage="expected")
    r = env.apply("fan1", control_type="curve", curve_sensor="temp2", curve_points=CURVE)
    assert r.json()["error"]["code"] == "curve_sensor_unavailable"
    bad = [{"temperature_c": 20, "rpm": 500}, {"temperature_c": 30, "rpm": 1400}]
    r = env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "curve_last_point_not_max"
    r = env.apply("fan1", control_type="fixed", percent=101)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
    r = env.apply("fan1", control_type="curve", curve_sensor="temp1",
                  curve_points=[{"temperature_c": 20, "rpm": 500}] * 7)
    assert r.status_code == 422
    # nothing was stored by rejected requests
    cfg = env.client.get("/api/v1/config").json()
    assert cfg["fans"][0]["control_type"] == "fixed" and cfg["fans"][0]["percent"] == 100


def test_curve_needs_max_rpm(env):
    env.login()
    env.tick()
    env.put_fan("fan1", usage="expected")
    env.put_sensor("temp1", usage="expected")
    r = env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    assert r.json()["error"]["code"] == "curve_max_rpm_missing"


def test_undetected_fan_rejected(env):
    env.login()
    env.tick()
    env.put_fan("fan2", usage="expected", max_rpm=1000)
    r = env.apply("fan2", control_type="fixed", percent=50)
    assert r.json()["error"]["code"] == "fan_not_detected"


def test_revision_conflict_409(env):
    env.login()
    env.tick()
    rev = env.revision()
    assert env.put_sensor("temp1", usage="expected", revision=rev).status_code == 200
    r = env.put_sensor("temp1", usage="unused", name="other window", revision=rev)
    assert r.status_code == 409
    assert r.json()["error"] == {"code": "revision_conflict", "details": {"current_revision": rev + 1}}


def test_failed_apply_keeps_last_valid_config(env):
    env.login()
    env.tick()
    activate(env)
    env.apply("fan1", control_type="fixed", percent=70)
    env.sim.faults.corrupt_responses = 1
    r = env.apply("fan1", control_type="fixed", percent=20)
    assert r.status_code == 502
    err = r.json()["error"]
    assert err["code"] == "apply_failed" and err["details"]["uncertain"] is True
    cfg = env.client.get("/api/v1/config").json()
    assert cfg["fans"][0]["percent"] == 70
    last = fan(env.state(), "fan1")["last_command"]
    assert last["ok"] is False and last["uncertain"] is True and last["command"]["percent"] == 20


def test_apply_while_disconnected_is_not_reported_as_done(env):
    env.login()
    env.tick()
    activate(env)
    env.sim.faults.unplugged = True
    env.tick()
    env.clock.advance(1)
    r = env.apply("fan1", control_type="fixed", percent=50)
    assert r.status_code == 503 and r.json()["error"]["code"] == "device_unavailable"


def test_sensor_used_by_curve_cannot_be_unused(env):
    env.login()
    env.tick()
    activate(env)
    env.put_sensor("temp1", usage="expected")
    env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    r = env.put_sensor("temp1", usage="unused")
    assert r.json()["error"]["code"] == "sensor_in_use_by_curve"


def test_max_rpm_change_conflicting_with_curve_rejected(env):
    env.login()
    env.tick()
    activate(env)
    env.put_sensor("temp1", usage="expected")
    env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    r = env.put_fan("fan1", usage="expected", max_rpm=1400)
    assert r.json()["error"]["code"] == "curve_conflicts_max_rpm"


def test_curve_check_endpoint(env):
    env.login()
    r = env.client.post("/api/v1/curves/check", headers=env.h, json={"max_rpm": 1500, "curve_points": CURVE})
    assert r.status_code == 200 and len(r.json()["effective_curve"]) == 6
    r = env.client.post("/api/v1/curves/check", headers=env.h,
                        json={"max_rpm": 1500, "curve_points": [{"temperature_c": 30, "rpm": 1500},
                                                                {"temperature_c": 20, "rpm": 1500}]})
    assert r.status_code == 422 and r.json()["error"]["code"] == "curve_temperatures_not_ascending"


def test_nan_in_json_rejected(env):
    env.login()
    raw = '{"max_rpm": 1500, "curve_points": [{"temperature_c": NaN, "rpm": 1500}]}'
    r = env.client.post("/api/v1/curves/check", content=raw,
                        headers={**env.h, "content-type": "application/json"})
    assert r.status_code == 422


def test_unknown_channel_and_api_path(env):
    env.login()
    assert env.put_fan("fan7", usage="expected").status_code == 404
    r = env.client.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")


# --- start-up, reconnect, persistence -------------------------------------------------

def test_reconnect_requests_full_power_first(env):
    env.login()
    env.tick()
    activate(env)
    env.apply("fan1", control_type="fixed", percent=30)
    env.sim.faults.unplugged = True
    env.tick()
    env.sim.faults.unplugged = False
    env.sim.sent.clear()
    env.tick()  # reconnect
    commands = [f[0] for f in env.sim.sent]
    assert 0x23 in commands
    first_write = commands.index(0x23)
    assert env.sim.sent[first_write][1:3] == bytes([0, 100])
    assert first_write < commands.index(0x10)  # before the population check
    assert env.sim.fans[0].percent == 100
    # the stored profile is unchanged, the last command shows the fail-safe
    assert env.client.get("/api/v1/config").json()["fans"][0]["percent"] == 30
    assert fan(env.state(), "fan1")["last_command"]["command"]["reason"] == "reconnect"


def test_stored_settings_reapplied_after_restart(tmp_path):
    gen = make_env(tmp_path)
    env = next(gen)
    env.login()
    env.tick()
    activate(env)
    env.put_sensor("temp1", usage="expected")
    env.apply("fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    next(gen, None)

    settings = test_settings(tmp_path / "data")
    from comcontrol.hardware.simulator import make_preset

    sim = make_preset("minimal")
    app, rt, db = build(settings, simulator=sim)
    rt._loop = _noop
    with TestClient(app) as client:
        client.portal.call(rt.tick)
        assert sim.fans[0].curve is not None
        assert sim.fans[0].curve[0] == 0 and sim.fans[0].curve[1][-1] == (3004, 1500)
    db.close()


async def _noop():
    return None


def test_minute_aggregation_and_restart_merge(env):
    env.login()
    t0 = datetime(2026, 11, 11, 12, 0, 5, tzinfo=UTC)
    now = [t0]
    env.rt.wall = lambda: now[0]
    for i in range(3):
        now[0] = t0 + timedelta(seconds=5 * i)
        env.sim.sensors[0].temperature = 20.0 + i
        env.tick()
    now[0] = t0 + timedelta(seconds=60)  # next minute -> previous one flushed
    env.tick()
    rows = env.rt.db.query("SELECT * FROM measurements_minute WHERE source = 'temp1' ORDER BY minute")
    assert len(rows) == 1
    r = rows[0]
    assert r["minute"] == int(datetime(2026, 11, 11, 12, 0, tzinfo=UTC).timestamp())
    assert (r["avg"], r["min"], r["max"], r["count"]) == (21.0, 20.0, 22.0, 3)
    # unconnected sensors leave gaps instead of 0 rows
    assert not env.rt.db.query("SELECT * FROM measurements_minute WHERE source = 'temp2'")
    # flushing the same minute again merges (restart within a minute)
    env.rt.aggregator.minute = r["minute"]
    env.rt.aggregator._acc = {"temp1": [30.0, 30.0, 30.0, 1]}
    env.rt.aggregator.flush()
    r = env.rt.db.one("SELECT * FROM measurements_minute WHERE source = 'temp1' AND minute = ?", (r["minute"],))
    assert (r["avg"], r["min"], r["max"], r["count"]) == (23.25, 20.0, 30.0, 4)  # (3*21+30)/4


def test_retention_365_days(env):
    db = env.rt.db
    now = datetime(2026, 11, 11, tzinfo=UTC)
    old = int((now - timedelta(days=366)).timestamp())
    keep = int((now - timedelta(days=364)).timestamp())
    with db.tx() as c:
        for minute in (old, keep):
            c.execute("INSERT INTO measurements_minute VALUES (?, 'temp1', 1, 1, 1, 1)", (minute,))
    purge_history(db, now, 365)
    assert [r["minute"] for r in db.query("SELECT minute FROM measurements_minute")] == [keep]


def test_config_survives_restart(tmp_path):
    gen = make_env(tmp_path)
    env = next(gen)
    env.login()
    env.put_sensor("temp1", usage="expected", name="Inlet", alarm_max_c=40)
    rev = env.revision()
    next(gen, None)
    app, rt, db = build(test_settings(tmp_path / "data"))
    with TestClient(app) as client:
        client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        cfg = client.get("/api/v1/config").json()
        assert cfg["revision"] == rev
        assert cfg["sensors"][0] == {"id": "temp1", "port": 1, "name": "Inlet", "usage": "expected",
                                     "alarm_max_c": 40.0}
    db.close()


def test_device_info_and_simulation_visible(env):
    env.login()
    env.tick()
    d = env.client.get("/api/v1/device").json()
    assert d["simulated"] is True and d["connection"] == "connected" and d["firmware"] == "0.9.212"
    r = env.client.post("/api/v1/device/assign", headers=env.h, json={"revision": env.revision(), "usb_path": "1-4"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_active"


def test_concurrent_requests_do_not_interleave_device_commands(env):
    """All hardware access goes through one worker thread."""
    import threading

    env.login()
    env.tick()
    activate(env)
    active = []
    overlaps = []
    inner = env.sim._handle

    def handle(frame):
        active.append(1)
        if len(active) > 1:
            overlaps.append(frame[0])
        try:
            return inner(frame)
        finally:
            active.pop()

    env.sim._handle = handle
    env.sim._lock = _NoLock()  # make the simulator itself non-serialising

    def worker():
        for _ in range(5):
            env.tick()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == []


class _NoLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
