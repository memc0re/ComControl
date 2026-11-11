"""FastAPI application: /api/v1 JSON API and (optionally) the static UI."""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .. import history
from ..auth import AuthService, LoginRateLimiter, PasswordPolicyError, Session
from ..config_store import FanConfig, RevisionConflict, SensorConfig
from ..curves import CurveError, effective_curve
from ..db import iso
from ..live import Reading
from ..runtime import ControlError, Runtime
from . import models as m

log = logging.getLogger("comcontrol.api")

SESSION_COOKIE = "comcontrol_session"
CSRF_HEADER = "x-csrf-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Client-side routes of the single page application (others get a real 404).
SPA_ROUTES = {"", "login", "channels", "curves", "history", "alarms", "settings"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, headers: dict | None = None, **details: object) -> None:
        super().__init__(code)
        self.status, self.code, self.details, self.headers = status, code, details, headers


def _error(status: int, code: str, details: dict | None = None, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "details": details or {}}}, status, headers=headers)


# --- dependencies ------------------------------------------------------------
def runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def auth_service(request: Request) -> AuthService:
    return request.app.state.auth


def check_origin(request: Request) -> None:
    """Reject cross-site state-changing requests (also for the login)."""
    if request.method not in UNSAFE_METHODS:
        return
    if request.headers.get("sec-fetch-site", "same-origin") not in ("same-origin", "none"):
        if not _origin_allowed(request):
            raise ApiError(403, "origin_not_allowed")
    origin = request.headers.get("origin")
    if origin is not None and not _origin_allowed(request):
        raise ApiError(403, "origin_not_allowed")


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return False
    host = request.headers.get("host", "")
    allowed = {f"http://{host}", *request.app.state.settings.allowed_origins}
    return origin in allowed


def require_session(
    request: Request, auth: Annotated[AuthService, Depends(auth_service)]
) -> Session:
    session = auth.get_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise ApiError(401, "not_authenticated")
    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), session.csrf_token.encode()):
            raise ApiError(403, "csrf_invalid")
    return session


SessionDep = Annotated[Session, Depends(require_session)]
RuntimeDep = Annotated[Runtime, Depends(runtime)]
AuthDep = Annotated[AuthService, Depends(auth_service)]


# --- conversions ---------------------------------------------------------------
def _ts(dt: datetime | None) -> str | None:
    return iso(dt) if dt else None


def _measurement(rt: Runtime, r: Reading, unit: str) -> m.Measurement:
    now = rt.mono()
    status = r.status(now, rt.live.inventory_age(now), rt.settings.stale_after)
    current = status == "ok"
    return m.Measurement(
        value=float(r.value) if current and r.value is not None else None,
        unit=unit,
        status=status,  # type: ignore[arg-type]
        error=r.error,
        measured_at=_ts(r.valid_at) if current else None,
        last_value=float(r.value) if r.value is not None else None,
        last_valid_at=_ts(r.valid_at),
        age_s=round(now - r.valid_mono, 1) if r.valid_mono is not None else None,
    )


def _channel_status(usage: str, reading_status: str) -> str:
    if usage == "unused":
        return "unused"
    return {
        "ok": "present",
        "not_connected": "expected_missing",
        "error": "error",
        "stale": "stale",
        "no_data": "no_data",
    }[reading_status]


def _points(points) -> list[m.CurvePoint] | None:
    if points is None:
        return None
    return [m.CurvePoint(temperature_c=t, rpm=r) for t, r in points]


def _fan_out(f: FanConfig) -> m.FanConfigOut:
    eff, eff_err = None, None
    if f.curve_points:
        try:
            eff = effective_curve(f.curve_points, max_rpm=f.max_rpm)
        except CurveError as exc:
            eff_err = exc.code
    return m.FanConfigOut(
        id=f.id, port=f.port, name=f.name, usage=f.usage, max_rpm=f.max_rpm,  # type: ignore[arg-type]
        dc_start_percent=f.dc_start_percent, fan_mode=f.fan_mode,  # type: ignore[arg-type]
        control_type=f.control_type, percent=f.percent,  # type: ignore[arg-type]
        curve_sensor=f"temp{f.curve_sensor}" if f.curve_sensor else None,
        curve_points=_points(f.curve_points), effective_curve=_points(eff), effective_curve_error=eff_err,
    )


def _sensor_out(s: SensorConfig) -> m.SensorConfigOut:
    return m.SensorConfigOut(id=s.id, port=s.port, name=s.name, usage=s.usage, alarm_max_c=s.alarm_max_c)  # type: ignore[arg-type]


def _outcome(o) -> m.CommandOutcomeOut | None:
    if o is None:
        return None
    return m.CommandOutcomeOut(ok=o.ok, uncertain=o.uncertain, error=o.error, steps=o.steps)


def _fan_port(fan_id: str) -> int:
    if len(fan_id) == 4 and fan_id.startswith("fan") and fan_id[3] in "123456":
        return int(fan_id[3])
    raise ApiError(404, "channel_not_found")


def _sensor_port(sensor_id: str) -> int:
    if len(sensor_id) == 5 and sensor_id.startswith("temp") and sensor_id[4] in "1234":
        return int(sensor_id[4])
    raise ApiError(404, "channel_not_found")


def _session_info(auth: AuthService, session: Session) -> m.SessionInfo:
    admin = auth.admin()
    return m.SessionInfo(
        username=admin["username"],
        password_is_default=admin["password_is_default"],
        csrf_token=session.csrf_token,
        expires_at=iso(datetime.fromtimestamp(session.expires_at, UTC)),
    )


# --- routes ------------------------------------------------------------------------
api = APIRouter(prefix="/api/v1", dependencies=[Depends(check_origin)])


@api.get("/health", response_model=m.Health)
def health(rt: RuntimeDep) -> m.Health:
    return m.Health(
        backend="ok",
        hardware="ok" if rt.connection.state == "connected" else "unavailable",
        simulated=rt.simulated,
    )


@api.post("/auth/login", response_model=m.SessionInfo, responses={401: {"model": m.ErrorResponse}})
def login(body: m.LoginRequest, request: Request, response: Response, auth: AuthDep) -> m.SessionInfo:
    limiter: LoginRateLimiter = request.app.state.login_limiter
    client = request.client.host if request.client else "unknown"
    wait = limiter.retry_after(client)
    if wait is not None:
        raise ApiError(429, "login_rate_limited", headers={"Retry-After": str(int(wait) + 1)},
                       retry_after_s=int(wait) + 1)
    if not auth.check_credentials(body.username, body.password):
        limiter.failure(client)
        log.info("failed login from %s", client)
        raise ApiError(401, "invalid_credentials")
    limiter.success(client)
    token, session = auth.create_session()
    response.set_cookie(
        SESSION_COOKIE, token, max_age=int(auth.session_seconds), path="/",
        httponly=True, samesite="strict", secure=False,
    )
    log.info("login from %s", client)
    return _session_info(auth, session)


@api.post("/auth/logout", status_code=204)
def logout(session: SessionDep, auth: AuthDep, response: Response) -> Response:
    auth.end_session(session)
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    response.status_code = 204
    return response


@api.get("/auth/session", response_model=m.SessionInfo)
def get_session(session: SessionDep, auth: AuthDep) -> m.SessionInfo:
    return _session_info(auth, session)


@api.post("/auth/password", status_code=204)
def change_password(body: m.PasswordChange, session: SessionDep, auth: AuthDep) -> Response:
    try:
        auth.change_password(session, body.current_password, body.new_password)
    except PasswordPolicyError as exc:
        raise ApiError(400 if exc.code != "current_password_wrong" else 403, exc.code) from exc
    return Response(status_code=204)


@api.get("/device", response_model=m.DeviceInfo)
def device(_: SessionDep, rt: RuntimeDep) -> m.DeviceInfo:
    c = rt.connection
    return m.DeviceInfo(
        simulated=rt.simulated, connection=c.state,  # type: ignore[arg-type]
        error_code=c.error_code, error_details=c.error_details, since=_ts(c.since),
        usb_path=c.usb_path, assigned_usb_path="simulated" if rt.simulated else rt.store.device_usb_path(),
        firmware=c.firmware if c.state == "connected" else None,
        bootloader=c.bootloader if c.state == "connected" else None,
    )


@api.get("/device/candidates", response_model=m.DeviceCandidates)
async def device_candidates(_: SessionDep, rt: RuntimeDep) -> m.DeviceCandidates:
    return m.DeviceCandidates(usb_paths=await rt.list_candidates())


@api.post("/device/assign", response_model=m.RevisionOnly)
async def device_assign(body: m.DeviceAssign, _: SessionDep, rt: RuntimeDep) -> m.RevisionOnly:
    return m.RevisionOnly(revision=await rt.assign_device(body.usb_path, body.revision))


@api.get("/state", response_model=m.LiveStateResponse)
def state(_: SessionDep, rt: RuntimeDep) -> m.LiveStateResponse:
    """Cached live state; never triggers a hardware request."""
    live = rt.live
    now = rt.mono()
    sensors = []
    for cfg in rt.store.sensors():
        r = live.sensors[cfg.port - 1]
        meas = _measurement(rt, r, "°C")
        sensors.append(m.SensorState(
            id=cfg.id, port=cfg.port, name=cfg.name, usage=cfg.usage,  # type: ignore[arg-type]
            status=_channel_status(cfg.usage, meas.status),  # type: ignore[arg-type]
            detected=r.present, temperature=meas, alarm_max_c=cfg.alarm_max_c,
        ))
    commands = rt.store.last_commands()
    latches = rt.safety.latches()
    fans = []
    for cfg in rt.store.fans():
        r = live.fans[cfg.port - 1]
        meas = _measurement(rt, r, "rpm")
        cmd = commands.get(cfg.port)
        latch = latches.get(cfg.port)
        fans.append(m.FanState(
            id=cfg.id, port=cfg.port, name=cfg.name, usage=cfg.usage,  # type: ignore[arg-type]
            status=_channel_status(cfg.usage, meas.status),  # type: ignore[arg-type]
            detected_mode=live.fan_modes[cfg.port - 1],  # type: ignore[arg-type]
            detected_mode_raw=live.fan_modes_raw[cfg.port - 1],
            rpm=meas, last_command=m.LastCommand(**cmd) if cmd else None,
            failsafe=m.FailsafeState(
                reason=latch.reason, source=latch.source, since=latch.since,
                delivered=latch.delivered, last_error=latch.last_error,
            ) if latch else None,
        ))
    counts = rt.db.one(
        "SELECT COUNT(*) AS active, SUM(acknowledged_at IS NULL) AS unack FROM alarms WHERE ended_at IS NULL"
    )
    rails = [
        m.RailState(id=f"rail_{name}", measurement=_measurement(rt, live.rails[i], "V"))
        for i, name in enumerate(("12v", "5v", "3v3"))
    ]
    age = live.inventory_age(now)
    return m.LiveStateResponse(
        simulated=rt.simulated, connection=rt.connection.state,  # type: ignore[arg-type]
        connection_error=rt.connection.error_code,
        data_age_s=round(age, 1) if age is not None else None,
        stale_after_s=rt.settings.stale_after,
        active_alarms=counts["active"] or 0, unacknowledged_alarms=counts["unack"] or 0,
        sensors=sensors, fans=fans, rails=rails,
    )


@api.get("/config", response_model=m.ConfigResponse)
def config(_: SessionDep, rt: RuntimeDep) -> m.ConfigResponse:
    return m.ConfigResponse(
        revision=rt.store.revision(),
        fans=[_fan_out(f) for f in rt.store.fans()],
        sensors=[_sensor_out(s) for s in rt.store.sensors()],
    )


@api.put("/fans/{fan_id}", response_model=m.FanUpdateResponse)
async def update_fan(fan_id: str, body: m.FanSettingsUpdate, _: SessionDep, rt: RuntimeDep) -> m.FanUpdateResponse:
    result = await rt.update_fan_settings(
        _fan_port(fan_id), body.revision, name=body.name.strip(), usage=body.usage,
        max_rpm=body.max_rpm, dc_start_percent=body.dc_start_percent,
    )
    return m.FanUpdateResponse(revision=result.revision, fan=_fan_out(result.config), outcome=_outcome(result.outcome))


@api.post("/fans/{fan_id}/apply", response_model=m.FanUpdateResponse)
async def apply_fan(fan_id: str, body: m.FanControlApply, _: SessionDep, rt: RuntimeDep) -> m.FanUpdateResponse:
    port = _fan_port(fan_id)
    if body.control_type == "curve" and (body.curve_sensor is None or body.curve_points is None):
        raise ApiError(422, "curve_missing")
    result = await rt.apply_fan_control(
        port, body.revision, fan_mode=body.fan_mode, control_type=body.control_type,
        percent=body.percent,
        curve_sensor=_sensor_port(body.curve_sensor) if body.curve_sensor else None,
        curve_points=[(p.temperature_c, p.rpm) for p in body.curve_points] if body.curve_points else None,
    )
    return m.FanUpdateResponse(revision=result.revision, fan=_fan_out(result.config), outcome=_outcome(result.outcome))


@api.put("/sensors/{sensor_id}", response_model=m.SensorUpdateResponse)
async def update_sensor(
    sensor_id: str, body: m.SensorSettingsUpdate, _: SessionDep, rt: RuntimeDep
) -> m.SensorUpdateResponse:
    rev, sensor = await rt.update_sensor_settings(
        _sensor_port(sensor_id), body.revision, name=body.name.strip(), usage=body.usage,
        alarm_max_c=body.alarm_max_c,
    )
    return m.SensorUpdateResponse(revision=rev, sensor=_sensor_out(sensor))


@api.post("/curves/check", response_model=m.CurveCheckResponse)
def check_curve(body: m.CurveCheck, _: SessionDep) -> m.CurveCheckResponse:
    """Validate a curve and show its effective six-point form (no hardware access)."""
    try:
        eff = effective_curve([(p.temperature_c, p.rpm) for p in body.curve_points], max_rpm=body.max_rpm)
    except CurveError as exc:
        raise ApiError(422, exc.code, **exc.details) from exc
    return m.CurveCheckResponse(effective_curve=_points(eff) or [])


def _alarm(row) -> m.Alarm:
    return m.Alarm(
        id=row["id"], type=row["type"], source=row["source"], started_at=row["started_at"],
        ended_at=row["ended_at"], acknowledged_at=row["acknowledged_at"],
        active=row["ended_at"] is None, details=json.loads(row["details"]),
    )


@api.get("/alarms", response_model=m.AlarmList)
def alarms(
    _: SessionDep, rt: RuntimeDep,
    state: Literal["active", "all"] = "active",
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> m.AlarmList:
    where = "WHERE ended_at IS NULL" if state == "active" else ""
    rows = rt.db.query(f"SELECT * FROM alarms {where} ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))
    return m.AlarmList(alarms=[_alarm(r) for r in rows])


@api.post("/alarms/{alarm_id}/acknowledge", response_model=m.Alarm)
def acknowledge_alarm(alarm_id: int, _: SessionDep, rt: RuntimeDep) -> m.Alarm:
    if not rt.safety.acknowledge(alarm_id):
        raise ApiError(404, "alarm_not_found")
    return _alarm(rt.db.one("SELECT * FROM alarms WHERE id = ?", (alarm_id,)))


@api.post("/failsafe/release", response_model=m.FailsafeRelease)
async def release_failsafe(_: SessionDep, rt: RuntimeDep) -> m.FailsafeRelease:
    """Explicit return to the configured profiles, only where the cause has ended."""
    released, blocked, failed = await rt.release_failsafe()
    body = m.FailsafeRelease(
        released=[f"fan{p}" for p in released], blocked=[f"fan{p}" for p in blocked],
        failed=[f"fan{p}" for p in failed],
    )
    if not released and (blocked or failed):
        code = "failsafe_cause_active" if blocked else "failsafe_release_failed"
        raise ApiError(409, code, blocked=body.blocked, failed=body.failed)
    return body


@api.get("/history", response_model=m.HistoryResponse)
def get_history(
    _: SessionDep, rt: RuntimeDep,
    sources: Annotated[str, Query(max_length=200)],
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    max_points: Annotated[int, Query(ge=1, le=history.MAX_POINTS)] = 600,
) -> dict:
    end = to or datetime.now(UTC)
    start = from_ or end - timedelta(hours=24)
    try:
        return history.query(rt.db, [s.strip() for s in sources.split(",") if s.strip()], start, end, max_points)
    except history.HistoryError as exc:
        raise ApiError(422, exc.code, **exc.details) from exc


@api.get("/events", response_model=m.EventList)
def events(_: SessionDep, rt: RuntimeDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> m.EventList:
    rows = rt.db.query("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
    return m.EventList(events=[
        m.Event(id=r["id"], at=r["at"], kind=r["kind"], details=json.loads(r["details"])) for r in rows
    ])


@api.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def api_not_found(rest: str) -> None:
    raise ApiError(404, "not_found")


# --- application ---------------------------------------------------------------------
def create_app(rt: Runtime, auth: AuthService, limiter: LoginRateLimiter, *, start_polling: bool = True) -> FastAPI:
    settings = rt.settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        auth.ensure_default_admin()
        await rt.start(background=start_polling)
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(
        title="ComControl", version="0.1.0", lifespan=lifespan,
        docs_url="/api/v1/docs", redoc_url=None, openapi_url="/api/v1/openapi.json",
    )
    app.state.runtime = rt
    app.state.auth = auth
    app.state.login_limiter = limiter
    app.state.settings = settings
    if settings.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.details, exc.headers)

    @app.exception_handler(ControlError)
    async def _control_error(_: Request, exc: ControlError) -> JSONResponse:
        return _error(exc.status, exc.code, exc.details)

    @app.exception_handler(RevisionConflict)
    async def _conflict(_: Request, exc: RevisionConflict) -> JSONResponse:
        return _error(409, "revision_conflict", {"current_revision": exc.current})

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "type": e.get("type"), "ctx": {k: str(v) for k, v in (e.get("ctx") or {}).items()}}
            for e in exc.errors()
        ]
        return _error(422, "validation_error", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code)

    if rt.simulated:
        app.include_router(_simulator_router())
    app.include_router(api)
    if settings.static_dir is not None:
        _mount_static(app, settings.static_dir)
    return app


def _simulator_router() -> APIRouter:
    """Fault injection for development and UI tests; exists only in simulation mode."""
    router = APIRouter(prefix="/api/v1/simulator", dependencies=[Depends(check_origin)])

    @router.post("", status_code=204, include_in_schema=False)
    def control_simulator(body: m.SimulatorControl, _: SessionDep, rt: RuntimeDep) -> Response:
        sim = rt.adapter.connector.sim  # type: ignore[attr-defined]
        for sid, temp in body.sensor_temperatures.items():
            sim.sensors[_sensor_port(sid) - 1].temperature = temp
        for sid, connected in body.sensors_connected.items():
            sim.sensors[_sensor_port(sid) - 1].connected = connected
        for fid, blocked in body.fans_blocked.items():
            sim.fans[_fan_port(fid) - 1].blocked = blocked
        if body.unplugged is not None:
            sim.faults.unplugged = body.unplugged
        return Response(status_code=204)

    return router


def _mount_static(app: FastAPI, static_dir: Path) -> None:
    root = static_dir.resolve()
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def static(path: str) -> Response:
        if path.startswith("api/") or path == "api":
            raise ApiError(404, "not_found")
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        if path.rstrip("/") in SPA_ROUTES and index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        raise ApiError(404, "not_found")
