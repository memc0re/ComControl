<script lang="ts">
	// Effective hardware curve: flat below the first and above the last point,
	// linear in between (as observed on the controller).
	import type { CurvePoint } from '#lib/api.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let { points, currentTemp = null }: { points: CurvePoint[]; currentTemp?: number | null } = $props();

	const W = 480, H = 220, L = 52, R = 12, T = 12, B = 32;
	const tMin = $derived(Math.max(0, Math.floor(Math.min(points[0]?.temperature_c ?? 0, currentTemp ?? 99) - 5)));
	const tMax = $derived(Math.ceil(Math.max(points.at(-1)?.temperature_c ?? 60, currentTemp ?? 0) + 5));
	const rMax = $derived(Math.max(100, ...points.map((p) => p.rpm)) * 1.1);
	const x = (v: number) => L + ((v - tMin) / (tMax - tMin || 1)) * (W - L - R);
	const y = (v: number) => H - B - (v / rMax) * (H - T - B);
	const path = $derived.by(() => {
		if (!points.length) return '';
		const pts = [{ temperature_c: tMin, rpm: points[0].rpm }, ...points, { temperature_c: tMax, rpm: points.at(-1)!.rpm }];
		return pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.temperature_c).toFixed(1)},${y(p.rpm).toFixed(1)}`).join(' ');
	});
	const ticksX = $derived(Array.from({ length: 6 }, (_, i) => tMin + ((tMax - tMin) * i) / 5));
	const ticksY = $derived(Array.from({ length: 5 }, (_, i) => (rMax / 1.1) * (i / 4)));
</script>

<svg viewBox="0 0 {W} {H}" class="w-full max-w-xl" role="img" aria-label={t('ctl.effective')}>
	{#each ticksY as v (v)}
		<line x1={L} x2={W - R} y1={y(v)} y2={y(v)} class="stroke-border" />
		<text x={L - 4} y={y(v) + 4} text-anchor="end" class="fill-muted-foreground text-[10px]">{i18n.number(v)}</text>
	{/each}
	{#each ticksX as v (v)}
		<text x={x(v)} y={H - B + 14} text-anchor="middle" class="fill-muted-foreground text-[10px]">{i18n.number(v, 0)} °C</text>
	{/each}
	<path d={path} fill="none" class="stroke-primary" stroke-width="2" />
	{#each points as p, i (i)}
		<circle cx={x(p.temperature_c)} cy={y(p.rpm)} r="3.5" class="fill-primary" />
	{/each}
	{#if currentTemp !== null}
		<line x1={x(currentTemp)} x2={x(currentTemp)} y1={T} y2={H - B} class="stroke-warning" stroke-dasharray="4 3" />
	{/if}
</svg>
