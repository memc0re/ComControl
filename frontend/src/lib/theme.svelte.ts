import { readPref, writePref } from './prefs.ts';

export type Theme = 'light' | 'dark';

class ThemeState {
	current = $state<Theme>('light');

	init() {
		const stored = readPref('theme');
		const system =
			typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches
				? 'dark'
				: 'light';
		this.current = stored === 'light' || stored === 'dark' ? stored : system;
		this.apply();
	}

	toggle() {
		this.current = this.current === 'dark' ? 'light' : 'dark';
		writePref('theme', this.current);
		this.apply();
	}

	private apply() {
		document.documentElement.classList.toggle('dark', this.current === 'dark');
		document.documentElement.style.colorScheme = this.current;
	}
}

export const theme = new ThemeState();
