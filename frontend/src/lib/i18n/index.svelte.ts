import { de } from './de.ts';
import { en, type MessageKey } from './en.ts';
import { readPref, writePref } from '../prefs.ts';

export type Lang = 'de' | 'en';
export type { MessageKey };

const catalogs: Record<Lang, Record<MessageKey, string>> = { de, en };

export function browserLang(languages: readonly string[]): Lang {
	const first = languages[0] ?? '';
	return first.toLowerCase().startsWith('de') ? 'de' : 'en';
}

export function format(template: string, params: Record<string, unknown> = {}): string {
	return template.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
}

class I18n {
	lang = $state<Lang>('en');

	init() {
		const stored = readPref('lang');
		this.lang =
			stored === 'de' || stored === 'en'
				? stored
				: browserLang(typeof navigator === 'undefined' ? [] : navigator.languages);
		this.apply();
	}

	set(lang: Lang) {
		this.lang = lang;
		writePref('lang', lang);
		this.apply();
	}

	private apply() {
		if (typeof document !== 'undefined') document.documentElement.lang = this.lang;
	}

	get locale(): string {
		return this.lang === 'de' ? 'de-DE' : 'en-GB';
	}

	t = (key: MessageKey, params?: Record<string, unknown>): string =>
		format(catalogs[this.lang][key] ?? key, params);

	/** Translate an API/connection error code; unknown codes are shown generically. */
	error = (code: string, details: Record<string, unknown> = {}): string => {
		const key = `err.${code}` as MessageKey;
		if (key in catalogs[this.lang]) return this.t(key, details);
		const conn = `conn.${code}` as MessageKey;
		if (conn in catalogs[this.lang]) return this.t(conn, details);
		return this.t('err.generic', { code });
	};

	number = (value: number, digits = 0): string =>
		new Intl.NumberFormat(this.locale, {
			minimumFractionDigits: digits,
			maximumFractionDigits: digits
		}).format(value);

	dateTime = (iso: string | null | undefined): string =>
		iso
			? new Intl.DateTimeFormat(this.locale, { dateStyle: 'short', timeStyle: 'medium' }).format(
					new Date(iso)
				)
			: '–';

	time = (iso: string): string =>
		new Intl.DateTimeFormat(this.locale, { timeStyle: 'short' }).format(new Date(iso));
}

export const i18n = new I18n();
export const t = i18n.t;
