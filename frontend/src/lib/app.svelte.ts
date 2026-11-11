// Application state: session and the cached live state, refreshed every 5 s.
// Browsers only read the backend cache; they never trigger hardware access.
import { goto } from '$app/navigation';
import { api, ApiError, setCsrf, setUnauthenticatedHandler, type LiveState, type SessionInfo } from './api.ts';

export const POLL_MS = 5000;

class AppState {
	session = $state<SessionInfo | null>(null);
	checked = $state(false);
	live = $state<LiveState | null>(null);
	/** Backend unreachable (network error) since the last successful refresh. */
	backendLost = $state(false);
	lastRefresh = $state<number | null>(null);
	private timer: ReturnType<typeof setInterval> | null = null;

	constructor() {
		setUnauthenticatedHandler(() => this.loggedOut());
	}

	async checkSession() {
		try {
			this.setSession(await api.session());
		} catch {
			this.setSession(null);
		}
		this.checked = true;
	}

	setSession(s: SessionInfo | null) {
		this.session = s;
		setCsrf(s?.csrf_token ?? null);
		if (s) this.startPolling();
		else this.stopPolling();
	}

	async refresh() {
		try {
			this.live = await api.state();
			this.backendLost = false;
			this.lastRefresh = Date.now();
		} catch (e) {
			if (e instanceof ApiError && e.status === 0) this.backendLost = true;
		}
	}

	startPolling() {
		if (this.timer) return;
		void this.refresh();
		this.timer = setInterval(() => void this.refresh(), POLL_MS);
	}

	stopPolling() {
		if (this.timer) clearInterval(this.timer);
		this.timer = null;
	}

	loggedOut() {
		this.setSession(null);
		this.live = null;
		void goto('/login');
	}

	async logout() {
		try {
			await api.logout();
		} finally {
			this.loggedOut();
		}
	}
}

export const app = new AppState();
