// Visual preferences only (language, colour scheme). Credentials and session
// secrets are never stored in the browser.
const ALLOWED = new Set(['lang', 'theme']);

export function readPref(key: string): string | null {
	if (!ALLOWED.has(key)) throw new Error(`not a preference: ${key}`);
	try {
		return localStorage.getItem(`comcontrol.${key}`);
	} catch {
		return null;
	}
}

export function writePref(key: string, value: string): void {
	if (!ALLOWED.has(key)) throw new Error(`not a preference: ${key}`);
	try {
		localStorage.setItem(`comcontrol.${key}`, value);
	} catch {
		// storage unavailable: preference lasts for this page view only
	}
}
