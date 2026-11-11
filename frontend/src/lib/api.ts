// Typed client for the backend /api/v1 (same origin, cookie session + CSRF header).

export type Usage = 'unused' | 'expected';
export type ReadingStatus = 'ok' | 'not_connected' | 'error' | 'stale' | 'no_data';
export type ChannelStatus = 'unused' | 'present' | 'expected_missing' | 'error' | 'stale' | 'no_data';

export interface Measurement {
	value: number | null;
	unit: string;
	status: ReadingStatus;
	error: string | null;
	measured_at: string | null;
	last_value: number | null;
	last_valid_at: string | null;
	age_s: number | null;
}

export interface SensorState {
	id: string;
	port: number;
	name: string;
	usage: Usage;
	status: ChannelStatus;
	detected: boolean | null;
	temperature: Measurement;
	alarm_max_c: number | null;
}

export interface LastCommand {
	sent_at: string;
	command: Record<string, unknown>;
	ok: boolean;
	uncertain: boolean;
	error: string | null;
}

export interface Failsafe {
	reason: string;
	source: string;
	since: string;
	delivered: boolean;
	last_error: string | null;
}

export interface FanState {
	id: string;
	port: number;
	name: string;
	usage: Usage;
	status: ChannelStatus;
	detected_mode: 'disconnected' | 'dc' | 'pwm' | 'unknown' | null;
	detected_mode_raw: number | null;
	rpm: Measurement;
	last_command: LastCommand | null;
	failsafe: Failsafe | null;
}

export interface LiveState {
	simulated: boolean;
	connection: 'connected' | 'disconnected';
	connection_error: string | null;
	data_age_s: number | null;
	stale_after_s: number;
	active_alarms: number;
	unacknowledged_alarms: number;
	sensors: SensorState[];
	fans: FanState[];
	rails: { id: string; measurement: Measurement }[];
}

export interface CurvePoint {
	temperature_c: number;
	rpm: number;
}

export interface FanConfig {
	id: string;
	port: number;
	name: string;
	usage: Usage;
	max_rpm: number | null;
	dc_start_percent: number;
	fan_mode: 'auto' | 'dc' | 'pwm';
	control_type: 'fixed' | 'curve';
	percent: number;
	curve_sensor: string | null;
	curve_points: CurvePoint[] | null;
	effective_curve: CurvePoint[] | null;
	effective_curve_error: string | null;
}

export interface SensorConfig {
	id: string;
	port: number;
	name: string;
	usage: Usage;
	alarm_max_c: number | null;
}

export interface Config {
	revision: number;
	fans: FanConfig[];
	sensors: SensorConfig[];
}

export interface SessionInfo {
	username: string;
	password_is_default: boolean;
	csrf_token: string;
	expires_at: string;
}

export interface DeviceInfo {
	simulated: boolean;
	connection: 'connected' | 'disconnected';
	error_code: string | null;
	error_details: Record<string, unknown>;
	since: string | null;
	usb_path: string | null;
	assigned_usb_path: string | null;
	firmware: string | null;
	bootloader: string | null;
}

export interface Outcome {
	ok: boolean;
	uncertain: boolean;
	error: string | null;
	steps: Record<string, unknown>[];
}

export interface FanUpdate {
	revision: number;
	fan: FanConfig;
	outcome: Outcome | null;
}

export interface Alarm {
	id: number;
	type: 'over_temperature' | 'sensor_failure' | 'fan_stall' | 'fan_missing' | 'device_error';
	source: string;
	started_at: string;
	ended_at: string | null;
	acknowledged_at: string | null;
	active: boolean;
	details: Record<string, unknown>;
}

export interface HistorySeries {
	source: string;
	unit: string;
	points: { t: string; avg: number; min: number; max: number; count: number }[];
}

export interface History {
	from: string;
	to: string;
	bucket_s: number;
	series: HistorySeries[];
}

export interface EventEntry {
	id: number;
	at: string;
	kind: string;
	details: Record<string, unknown>;
}

export class ApiError extends Error {
	constructor(
		public status: number,
		public code: string,
		public details: Record<string, unknown> = {}
	) {
		super(code);
	}
}

let csrfToken: string | null = null;

export function setCsrf(token: string | null) {
	csrfToken = token;
}

/** Called on 401 so the app can return to the login page. */
let onUnauthenticated: () => void = () => {};
export function setUnauthenticatedHandler(fn: () => void) {
	onUnauthenticated = fn;
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
	const headers: Record<string, string> = { Accept: 'application/json' };
	if (body !== undefined) headers['Content-Type'] = 'application/json';
	if (method !== 'GET' && csrfToken) headers['X-CSRF-Token'] = csrfToken;
	let res: Response;
	try {
		res = await fetch(`/api/v1${path}`, {
			method,
			headers,
			body: body === undefined ? undefined : JSON.stringify(body),
			credentials: 'same-origin',
			cache: 'no-store'
		});
	} catch {
		throw new ApiError(0, 'network');
	}
	if (res.status === 204) return undefined as T;
	let data: unknown = null;
	try {
		data = await res.json();
	} catch {
		throw new ApiError(res.status, res.ok ? 'invalid_response' : 'network');
	}
	if (!res.ok) {
		const err = (data as { error?: { code?: string; details?: Record<string, unknown> } })?.error;
		const apiErr = new ApiError(res.status, err?.code ?? 'http_error', err?.details ?? {});
		if (res.status === 401 && path !== '/auth/login') onUnauthenticated();
		throw apiErr;
	}
	return data as T;
}

export const api = {
	login: (username: string, password: string) =>
		request<SessionInfo>('POST', '/auth/login', { username, password }),
	logout: () => request<void>('POST', '/auth/logout'),
	session: () => request<SessionInfo>('GET', '/auth/session'),
	changePassword: (current_password: string, new_password: string) =>
		request<void>('POST', '/auth/password', { current_password, new_password }),
	state: () => request<LiveState>('GET', '/state'),
	config: () => request<Config>('GET', '/config'),
	device: () => request<DeviceInfo>('GET', '/device'),
	candidates: () => request<{ usb_paths: string[] }>('GET', '/device/candidates'),
	assign: (revision: number, usb_path: string) =>
		request<{ revision: number }>('POST', '/device/assign', { revision, usb_path }),
	updateFan: (id: string, body: Record<string, unknown>) =>
		request<FanUpdate>('PUT', `/fans/${id}`, body),
	applyFan: (id: string, body: Record<string, unknown>) =>
		request<FanUpdate>('POST', `/fans/${id}/apply`, body),
	updateSensor: (id: string, body: Record<string, unknown>) =>
		request<{ revision: number; sensor: SensorConfig }>('PUT', `/sensors/${id}`, body),
	checkCurve: (max_rpm: number | null, curve_points: CurvePoint[]) =>
		request<{ effective_curve: CurvePoint[] }>('POST', '/curves/check', { max_rpm, curve_points }),
	alarms: (state: 'active' | 'all') => request<{ alarms: Alarm[] }>('GET', `/alarms?state=${state}`),
	acknowledge: (id: number) => request<Alarm>('POST', `/alarms/${id}/acknowledge`),
	release: () =>
		request<{ released: string[]; blocked: string[]; failed: string[] }>('POST', '/failsafe/release'),
	history: (sources: string[], from: Date, to: Date, maxPoints: number) =>
		request<History>(
			'GET',
			`/history?sources=${encodeURIComponent(sources.join(','))}&from=${encodeURIComponent(
				from.toISOString()
			)}&to=${encodeURIComponent(to.toISOString())}&max_points=${maxPoints}`
		),
	events: (limit = 50) => request<{ events: EventEntry[] }>('GET', `/events?limit=${limit}`)
};
