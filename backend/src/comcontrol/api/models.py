"""Typed request and response models of the /api/v1 contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Usage = Literal["unused", "expected"]
FanModeSetting = Literal["auto", "dc", "pwm"]
ReadingStatus = Literal["ok", "not_connected", "error", "stale", "no_data"]
ChannelStatus = Literal["unused", "present", "expected_missing", "error", "stale", "no_data"]
Name = Annotated[str, Field(max_length=64)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str
    details: dict = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


# --- auth ------------------------------------------------------------------
class LoginRequest(Strict):
    username: Annotated[str, Field(max_length=128)]
    password: Annotated[str, Field(max_length=256)]


class SessionInfo(BaseModel):
    username: str
    password_is_default: bool
    csrf_token: str
    expires_at: str


class PasswordChange(Strict):
    current_password: Annotated[str, Field(max_length=256)]
    new_password: Annotated[str, Field(max_length=256)]


# --- device / health --------------------------------------------------------
class Health(BaseModel):
    backend: Literal["ok"]
    hardware: Literal["ok", "unavailable"]
    simulated: bool


class DeviceInfo(BaseModel):
    simulated: bool
    connection: Literal["connected", "disconnected"]
    error_code: str | None
    error_details: dict
    since: str | None
    usb_path: str | None
    assigned_usb_path: str | None
    firmware: str | None
    bootloader: str | None


class DeviceCandidates(BaseModel):
    usb_paths: list[str]


class DeviceAssign(Strict):
    revision: int
    usb_path: Annotated[str, Field(pattern=r"^\d+-\d+(\.\d+)*$", max_length=64)]


class RevisionOnly(BaseModel):
    revision: int


# --- live state -------------------------------------------------------------
class Measurement(BaseModel):
    value: float | None
    unit: str
    status: ReadingStatus
    error: str | None
    measured_at: str | None  # UTC time of the current value
    last_value: float | None
    last_valid_at: str | None
    age_s: float | None


class SensorState(BaseModel):
    id: str
    port: int
    name: str
    usage: Usage
    status: ChannelStatus
    detected: bool | None
    temperature: Measurement
    alarm_max_c: float | None


class LastCommand(BaseModel):
    sent_at: str
    command: dict
    ok: bool
    uncertain: bool
    error: str | None


class FailsafeState(BaseModel):
    reason: str
    source: str
    since: str
    delivered: bool
    last_error: str | None


class FanState(BaseModel):
    id: str
    port: int
    name: str
    usage: Usage
    status: ChannelStatus
    detected_mode: Literal["disconnected", "dc", "pwm", "unknown"] | None
    detected_mode_raw: int | None
    rpm: Measurement
    last_command: LastCommand | None
    failsafe: FailsafeState | None


class RailState(BaseModel):
    id: str
    measurement: Measurement


class LiveStateResponse(BaseModel):
    simulated: bool
    connection: Literal["connected", "disconnected"]
    connection_error: str | None
    data_age_s: float | None
    stale_after_s: float
    active_alarms: int
    unacknowledged_alarms: int
    sensors: list[SensorState]
    fans: list[FanState]
    rails: list[RailState]


# --- configuration ----------------------------------------------------------
class CurvePoint(Strict):
    temperature_c: float
    rpm: int


class FanConfigOut(BaseModel):
    id: str
    port: int
    name: str
    usage: Usage
    max_rpm: int | None
    dc_start_percent: int
    fan_mode: FanModeSetting
    control_type: Literal["fixed", "curve"]
    percent: int
    curve_sensor: str | None
    curve_points: list[CurvePoint] | None
    effective_curve: list[CurvePoint] | None
    effective_curve_error: str | None


class SensorConfigOut(BaseModel):
    id: str
    port: int
    name: str
    usage: Usage
    alarm_max_c: float | None


class ConfigResponse(BaseModel):
    revision: int
    fans: list[FanConfigOut]
    sensors: list[SensorConfigOut]


class FanSettingsUpdate(Strict):
    revision: int
    name: Name = ""
    usage: Usage
    max_rpm: Annotated[int, Field(ge=1, le=65535)] | None = None
    dc_start_percent: Annotated[int, Field(ge=0, le=100)] = 50


class FanControlApply(Strict):
    revision: int
    fan_mode: FanModeSetting = "auto"
    control_type: Literal["fixed", "curve"]
    percent: Annotated[int, Field(ge=0, le=100)] | None = None
    curve_sensor: Annotated[str, Field(pattern=r"^temp[1-4]$")] | None = None
    curve_points: Annotated[list[CurvePoint], Field(min_length=1, max_length=6)] | None = None


class CommandOutcomeOut(BaseModel):
    ok: bool
    uncertain: bool
    error: str | None
    steps: list[dict]


class FanUpdateResponse(BaseModel):
    revision: int
    fan: FanConfigOut
    outcome: CommandOutcomeOut | None


class SensorSettingsUpdate(Strict):
    revision: int
    name: Name = ""
    usage: Usage
    alarm_max_c: Annotated[float, Field(ge=-40, le=150, allow_inf_nan=False)] | None = None


class SensorUpdateResponse(BaseModel):
    revision: int
    sensor: SensorConfigOut


class CurveCheck(Strict):
    max_rpm: Annotated[int, Field(ge=1, le=65535)] | None
    curve_points: Annotated[list[CurvePoint], Field(min_length=1, max_length=6)]


class CurveCheckResponse(BaseModel):
    effective_curve: list[CurvePoint]


# --- alarms, fail-safe, history, events ------------------------------------------
class Alarm(BaseModel):
    id: int
    type: Literal["over_temperature", "sensor_failure", "fan_stall", "fan_missing", "device_error"]
    source: str
    started_at: str
    ended_at: str | None
    acknowledged_at: str | None
    active: bool
    details: dict


class AlarmList(BaseModel):
    alarms: list[Alarm]


class FailsafeRelease(BaseModel):
    released: list[str]
    blocked: list[str]
    failed: list[str]


class HistoryPoint(BaseModel):
    t: str
    avg: float
    min: float
    max: float
    count: int


class HistorySeries(BaseModel):
    source: str
    unit: str
    points: list[HistoryPoint]


class HistoryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    bucket_s: int
    series: list[HistorySeries]


class Event(BaseModel):
    id: int
    at: str
    kind: str
    details: dict


class EventList(BaseModel):
    events: list[Event]


class SimulatorControl(Strict):
    sensor_temperatures: dict[str, Annotated[float, Field(ge=-20, le=150)]] = {}
    sensors_connected: dict[str, bool] = {}
    fans_blocked: dict[str, bool] = {}
    unplugged: bool | None = None
