"""Alarms, latched fail-safe reaction and history API (simulator)."""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from comcontrol.hardware.simulator import make_preset
from comcontrol.main import build

from .conftest import make_env, test_settings

CURVE = [{"temperature_c": 20, "rpm": 500}, {"temperature_c": 30, "rpm": 1500}]


def fan(state, fid):
    return next(f for f in state["fans"] if f["id"] == fid)


def alarms(env, state="active"):
    return env.client.get(f"/api/v1/alarms?state={state}").json()["alarms"]


def setup_fan(env, fid="fan1", max_rpm=1500, **apply):
    assert env.put_fan(fid, usage="expected", max_rpm=max_rpm).status_code == 200
    if apply:
        r = env.apply(fid, **apply)
        assert r.status_code == 200, r.text


def ready(env):
    env.login()
    env.tick()


# --- over-temperature ---------------------------------------------------------------

def test_over_temperature_latches_curve_fans(env_factory):
    env = env_factory("gaps")
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=40)
    env.put_sensor("temp3", usage="expected")
    setup_fan(env, "fan1", control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    setup_fan(env, "fan6", max_rpm=2000, control_type="fixed", percent=40)
    env.sim.sensors[0].temperature = 45.0
    env.tick()
    a = alarms(env)
    assert [(x["type"], x["source"]) for x in a] == [("over_temperature", "temp1")]
    s = env.state()
    assert fan(s, "fan1")["failsafe"]["reason"] == "over_temperature"
    assert fan(s, "fan1")["failsafe"]["delivered"] is True
    assert env.sim.fans[0].percent == 100 and env.sim.fans[0].curve is None
    assert fan(s, "fan6")["failsafe"] is None  # its control does not use temp1
    assert env.sim.fans[5].percent == 40


def test_over_temperature_without_curve_latches_all_expected(env_factory):
    env = env_factory("gaps")
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=40)
    setup_fan(env, "fan1", control_type="fixed", percent=30)
    setup_fan(env, "fan6", max_rpm=2000, control_type="fixed", percent=40)
    env.sim.sensors[0].temperature = 41.0
    env.tick()
    s = env.state()
    assert fan(s, "fan1")["failsafe"] and fan(s, "fan6")["failsafe"]
    assert fan(s, "fan3")["failsafe"] is None  # unused channel untouched


def test_no_duplicate_alarm_and_ack_does_not_end_it(env):
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=30)
    env.sim.sensors[0].temperature = 35
    for _ in range(4):
        env.clock.advance(5)
        env.tick()
    a = alarms(env, "all")
    assert len(a) == 1
    r = env.client.post(f"/api/v1/alarms/{a[0]['id']}/acknowledge", headers=env.h)
    assert r.status_code == 200 and r.json()["acknowledged_at"] and r.json()["active"]
    assert env.state()["unacknowledged_alarms"] == 0 and env.state()["active_alarms"] == 1
    # hysteresis: 29.5 °C is not yet 1 K below the limit
    env.sim.sensors[0].temperature = 29.5
    env.tick()
    assert alarms(env)
    env.sim.sensors[0].temperature = 28.5
    env.tick()
    assert alarms(env) == [] and alarms(env, "all")[0]["ended_at"]
    assert env.client.post("/api/v1/alarms/999/acknowledge", headers=env.h).status_code == 404


# --- latch, release, restart ----------------------------------------------------------

def test_latch_blocks_profile_change_and_needs_explicit_release(env):
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=40)
    setup_fan(env, control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    env.sim.sensors[0].temperature = 45
    env.tick()
    # normal profile change cannot lift the fail-safe
    r = env.apply("fan1", control_type="fixed", percent=20)
    assert r.status_code == 409 and r.json()["error"]["code"] == "failsafe_active"
    # release refused while the cause is active
    r = env.client.post("/api/v1/failsafe/release", headers=env.h)
    assert r.status_code == 409 and r.json()["error"]["code"] == "failsafe_cause_active"
    # cause gone: still latched until the admin releases
    env.sim.sensors[0].temperature = 25
    env.tick()
    assert alarms(env) == []
    assert fan(env.state(), "fan1")["failsafe"] is not None and env.sim.fans[0].percent == 100
    r = env.client.post("/api/v1/failsafe/release", headers=env.h)
    assert r.status_code == 200 and r.json()["released"] == ["fan1"]
    assert fan(env.state(), "fan1")["failsafe"] is None
    assert env.sim.fans[0].curve is not None  # configured profile restored


def test_latch_survives_restart(tmp_path):
    gen = make_env(tmp_path)
    env = next(gen)
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=40)
    setup_fan(env, control_type="fixed", percent=30)
    env.sim.sensors[0].temperature = 45
    env.tick()
    assert fan(env.state(), "fan1")["failsafe"]
    next(gen, None)

    sim = make_preset("minimal")  # fresh controller, e.g. after power cycle
    sim.fans[0].percent = 30
    app, rt, db = build(test_settings(tmp_path / "data"), simulator=sim)
    rt._loop = _noop
    with TestClient(app) as client:
        client.portal.call(rt.tick)
        assert sim.fans[0].percent == 100  # no re-apply of the 30 % profile
        client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"})
        f1 = next(f for f in client.get("/api/v1/state").json()["fans"] if f["id"] == "fan1")
        assert f1["failsafe"]["reason"] == "over_temperature"
    db.close()


async def _noop():
    return None


def test_failsafe_not_deliverable_is_reported(env):
    ready(env)
    env.put_sensor("temp1", usage="expected", alarm_max_c=40)
    setup_fan(env, control_type="fixed", percent=30)
    env.sim.sensors[0].temperature = 45
    env.sim.faults.corrupt_responses = 1  # the 100 % command gets no valid answer
    original = env.sim._handle

    def handle(frame):
        if frame[0] == 0x23 and frame[2] == 100:
            env.sim.faults.corrupt_responses = 1
        return original(frame)

    env.sim._handle = handle
    env.sim.faults.corrupt_responses = 0
    env.tick()
    f1 = fan(env.state(), "fan1")
    assert f1["failsafe"]["delivered"] is False and f1["failsafe"]["last_error"] == "invalid_response"
    env.sim._handle = original
    env.sim.faults.corrupt_responses = 0
    env.tick()  # retried on the next cycle
    assert fan(env.state(), "fan1")["failsafe"]["delivered"] is True


def test_connection_loss_marks_failsafe_undeliverable_and_alarms(env):
    ready(env)
    setup_fan(env, control_type="fixed", percent=30)
    env.sim.faults.unplugged = True
    env.tick()
    a = alarms(env)
    assert [(x["type"], x["details"]["code"]) for x in a] == [("device_error", "device_io_error")]
    env.tick()
    assert len(alarms(env, "all")) == 1  # no duplicates while it persists
    env.sim.faults.unplugged = False
    env.tick()
    assert alarms(env) == []
    f1 = fan(env.state(), "fan1")
    assert f1["failsafe"]["reason"] == "reconnect" and f1["failsafe"]["delivered"]
    # cause ended -> release allowed, profile restored
    r = env.client.post("/api/v1/failsafe/release", headers=env.h)
    assert r.json()["released"] == ["fan1"] and env.sim.fans[0].percent == 30


# --- sensor failure ---------------------------------------------------------------------

def test_sensor_failure_latches_fans_using_it(env):
    ready(env)
    env.put_sensor("temp1", usage="expected")
    setup_fan(env, control_type="curve", curve_sensor="temp1", curve_points=CURVE)
    env.sim.sensors[0].connected = False
    env.tick()
    assert [(x["type"], x["source"]) for x in alarms(env)] == [("sensor_failure", "temp1")]
    assert fan(env.state(), "fan1")["failsafe"]["reason"] == "sensor_failure"
    assert env.sim.fans[0].percent == 100


def test_unused_sensor_never_alarms(env):
    ready(env)
    env.sim.sensors[0].connected = False
    env.tick()
    assert alarms(env, "all") == []


# --- stall ------------------------------------------------------------------------------

def test_stall_after_three_valid_zero_readings(env):
    ready(env)
    setup_fan(env, control_type="fixed", percent=60)
    env.sim.fans[0].blocked = True
    env.tick()
    env.tick()
    assert alarms(env) == []
    env.sim.faults.status_error_reads = 0
    env.tick()
    a = alarms(env)
    assert [(x["type"], x["source"]) for x in a] == [("fan_stall", "fan1")]
    assert fan(env.state(), "fan1")["failsafe"]["reason"] == "fan_stall"


def test_missing_rpm_readings_do_not_count_as_zero(env):
    ready(env)
    setup_fan(env, control_type="fixed", percent=60)
    env.sim.fans[0].blocked = True
    env.tick()
    env.tick()
    env.sim.faults.unplugged = True
    env.tick()  # no reading
    env.sim.faults.unplugged = False
    assert not [x for x in alarms(env) if x["type"] == "fan_stall"]


def test_no_stall_alarm_for_deliberate_zero_percent(env):
    ready(env)
    setup_fan(env, control_type="fixed", percent=0)
    for _ in range(5):
        env.tick()
    assert alarms(env) == []


def test_dc_fan_below_start_threshold_no_stall(env_factory):
    env = env_factory("gaps")
    ready(env)
    setup_fan(env, "fan3", max_rpm=7400, control_type="fixed", percent=30)  # sim stalls below 45 %
    for _ in range(4):
        env.tick()
    assert alarms(env) == []
    # above the configured start threshold (50 %) a stall is a fault
    env.put_fan("fan3", usage="expected", max_rpm=7400, dc_start_percent=20)
    for _ in range(3):
        env.tick()
    assert [(x["type"], x["source"]) for x in alarms(env)] == [("fan_stall", "fan3")]


def test_expected_fan_missing_alarm(env):
    ready(env)
    env.put_fan("fan2", usage="expected", max_rpm=1000)
    env.tick()
    assert [(x["type"], x["source"]) for x in alarms(env)] == [("fan_missing", "fan2")]


# --- history ----------------------------------------------------------------------------

def insert(env, source, minute, avg, lo, hi, n):
    with env.rt.db.tx() as c:
        c.execute("INSERT INTO measurements_minute VALUES (?, ?, ?, ?, ?, ?)", (minute, source, avg, lo, hi, n))


def test_history_condensed_keeps_extremes(env):
    env.login()
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    base = int(t0.timestamp())
    for i in range(10):
        insert(env, "temp1", base + 60 * i, 20 + i, 19 + i, 21 + i, 12)
    r = env.client.get("/api/v1/history", params={
        "sources": "temp1,fan1", "from": t0.isoformat(), "to": (t0 + timedelta(minutes=10)).isoformat(),
        "max_points": 2,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bucket_s"] == 300
    pts = body["series"][0]["points"]
    assert [p["t"] for p in pts] == ["2026-11-01T00:00:00.000Z", "2026-11-01T00:05:00.000Z"]
    assert pts[0] == {"t": pts[0]["t"], "avg": 22.0, "min": 19.0, "max": 25.0, "count": 60}
    assert body["series"][1] == {"source": "fan1", "unit": "rpm", "points": []}  # gap, not zeros


def test_history_gaps_stay_gaps(env):
    env.login()
    t0 = datetime(2026, 11, 1, tzinfo=UTC)
    base = int(t0.timestamp())
    insert(env, "fan1", base, 1000, 990, 1010, 12)
    insert(env, "fan1", base + 600, 1100, 1090, 1110, 12)
    r = env.client.get("/api/v1/history", params={
        "sources": "fan1", "from": t0.isoformat(), "to": (t0 + timedelta(minutes=20)).isoformat()})
    assert len(r.json()["series"][0]["points"]) == 2


def test_history_limits(env):
    env.login()
    now = datetime.now(UTC)
    for params, code in [
        ({"sources": "temp9"}, "history_invalid_source"),
        ({"sources": "temp1", "from": (now - timedelta(days=400)).isoformat(), "to": now.isoformat()},
         "history_range_too_long"),
        ({"sources": "temp1", "from": now.isoformat(), "to": (now - timedelta(hours=1)).isoformat()},
         "history_invalid_range"),
    ]:
        r = env.client.get("/api/v1/history", params=params)
        assert r.status_code == 422 and r.json()["error"]["code"] == code
    assert env.client.get("/api/v1/history", params={"sources": "temp1", "max_points": 5000}).status_code == 422


def test_events_logged(env):
    ready(env)
    setup_fan(env, control_type="fixed", percent=50)
    kinds = [e["kind"] for e in env.client.get("/api/v1/events").json()["events"]]
    for k in ("backend_started", "device_connected", "fan_activated", "fan_control_applied"):
        assert k in kinds
