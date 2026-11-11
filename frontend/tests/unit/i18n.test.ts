// Both UI languages must be complete; run with `npm run test:unit`.
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { test } from 'node:test';
import { de } from '../../src/lib/i18n/de.ts';
import { en } from '../../src/lib/i18n/en.ts';

test('German and English have the same keys', () => {
	assert.deepEqual(Object.keys(de).sort(), Object.keys(en).sort());
});

test('no empty translations and identical placeholders', () => {
	for (const key of Object.keys(en) as (keyof typeof en)[]) {
		assert.ok(en[key].trim(), `en ${key} empty`);
		assert.ok(de[key].trim(), `de ${key} empty`);
		const ph = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
		assert.deepEqual(ph(de[key]), ph(en[key]), `placeholders differ for ${key}`);
	}
});

function files(dir: string): string[] {
	return readdirSync(dir).flatMap((f) => {
		const p = join(dir, f);
		return statSync(p).isDirectory() ? files(p) : [p];
	});
}

test('every literal key used in the UI exists', () => {
	const src = files(join(import.meta.dirname, '../../src')).filter((f) => /\.(svelte|ts)$/.test(f) && !f.includes('/i18n/'));
	const missing: string[] = [];
	for (const f of src) {
		for (const m of readFileSync(f, 'utf8').matchAll(/\bt\('([\w.]+)'/g)) {
			if (!(m[1] in en)) missing.push(`${f}: ${m[1]}`);
		}
	}
	assert.deepEqual(missing, []);
});
