<script lang="ts">
	// Mean line with min/max band. Missing buckets break the line (gaps are
	// never drawn as zero).
	import type { HistorySeries } from '#lib/api.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let { series, from, to, bucketS }: { series: HistorySeries; from: Date; to: Date; bucketS: number } = $props();

	const W = 720, H = 220, L = 56, R = 12, T = 10, B = 28;
	const t0 = $derived(from.getTime());
	const t1 = $derived(to.getTime());
	const lo = $derived(Math.min(...series.points.map((p) => p.min)));
	const hi = $derived(Math.max(...series.points.map((p) => p.max)));
	const pad = $derived((hi - lo) * 0.1 || 1);
	const yMin = $derived(lo - pad);
	const yMax = $derived(hi + pad);
	const x = (ms: number) => L + ((ms - t0) / (t1 - t0 || 1)) * (W - L - R);
	const y = (v: number) => H - B - ((v - yMin) / (yMax - yMin || 1)) * (H - T - B);

	// split into contiguous segments: a gap is more than one bucket between points
	const segments = $derived.by(() => {
		const out: HistorySeries['points'][] = [];
		let cur: HistorySeries['points'] = [];
		let prev = -Infinity;
		for (const p of series.points) {
			const ms = new Date(p.t).getTime();
			if (ms - prev > bucketS * 1000 * 1.5 && cur.length) {
				out.push(cur);
				cur = [];
			}
			cur.push(p);
			prev = ms;
		}
		if (cur.length) out.push(cur);
		return out;
	});
	const mid = (p: { t: string }) => new Date(p.t).getTime() + (bucketS * 1000) / 2;
	const line = (seg: HistorySeries['points']) =>
		seg.map((p, i) => `${i ? 'L' : 'M'}${x(mid(p)).toFixed(1)},${y(p.avg).toFixed(1)}`).join(' ');
	const band = (seg: HistorySeries['points']) =>
		seg.map((p, i) => `${i ? 'L' : 'M'}${x(mid(p)).toFixed(1)},${y(p.max).toFixed(1)}`).join(' ') +
		' ' +
		[...seg].reverse().map((p) => `L${x(mid(p)).toFixed(1)},${y(p.min).toFixed(1)}`).join(' ') +
		' Z';
	const digits = $derived(series.unit === 'rpm' ? 0 : series.unit === 'V' ? 2 : 1);
	const ticksY = $derived(Array.from({ length: 5 }, (_, i) => yMin + ((yMax - yMin) * i) / 4));
	const ticksX = $derived(Array.from({ length: 5 }, (_, i) => t0 + ((t1 - t0) * i) / 4));
	const span = $derived(t1 - t0);
	const fmtX = (ms: number) =>
		new Intl.DateTimeFormat(i18n.locale, span > 2 * 86400e3 ? { dateStyle: 'short' } : { timeStyle: 'short' }).format(ms);
</script>

<svg viewBox="0 0 {W} {H}" class="w-full" role="img" aria-label={t('hist.chartLabel', { s: series.source })}>
	{#each ticksY as v (v)}
		<line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="stroke-border" />
		<text x={L - 4} y={y(v) + 4} text-anchor="end" class="fill-muted-foreground text-[10px]">{i18n.number(v, digits)}</text>
	{/each}
	{#each ticksX as ms (ms)}
		<text x={x(ms)} y={H - 8} text-anchor="middle" class="fill-muted-foreground text-[10px]">{fmtX(ms)}</text>
	{/each}
	{#each segments as seg, i (i)}
		<path d={band(seg)} class="fill-primary/15" />
		<path d={line(seg)} fill="none" class="stroke-primary" stroke-width="1.5" />
		{#if seg.length === 1}
			<circle cx={x(mid(seg[0]))} cy={y(seg[0].avg)} r="2" class="fill-primary" />
		{/if}
	{/each}
</svg>
