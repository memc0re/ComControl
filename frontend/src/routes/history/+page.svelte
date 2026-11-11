<script lang="ts">
	import * as Card from '#lib/components/ui/card/index.ts';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import HistoryChart from '#lib/components/HistoryChart.svelte';
	import { api, type History } from '#lib/api.ts';
	import { app } from '#lib/app.svelte.ts';
	import { i18n, t, type MessageKey } from '#lib/i18n/index.svelte.ts';

	const ranges = { '1h': 3600, '24h': 86400, '7d': 7 * 86400, '30d': 30 * 86400, '365d': 365 * 86400 } as const;
	type Range = keyof typeof ranges;

	let range = $state<Range>('24h');
	let chosen = $state<string[]>([]);
	let data = $state<History | null>(null);
	let window_ = $state<{ from: Date; to: Date } | null>(null);
	let error = $state<unknown>(null);
	let loading = $state(false);

	const sources = $derived.by(() => {
		const live = app.live;
		if (!live) return [] as { id: string; label: string }[];
		const named = (id: string, name: string) => (name ? `${name} (${id})` : id);
		return [
			...live.sensors.filter((s) => s.usage === 'expected' || s.detected).map((s) => ({ id: s.id, label: named(s.id, s.name) })),
			...live.fans
				.filter((f) => f.usage === 'expected' || f.detected_mode === 'dc' || f.detected_mode === 'pwm')
				.map((f) => ({ id: f.id, label: named(f.id, f.name) })),
			...live.rails.map((r) => ({ id: r.id, label: t(`rail.${r.id}` as MessageKey) }))
		];
	});

	$effect(() => {
		if (!chosen.length && sources.length) chosen = sources.slice(0, 2).map((s) => s.id);
	});

	async function load() {
		if (!chosen.length) return;
		loading = true;
		const to = new Date();
		const from = new Date(to.getTime() - ranges[range] * 1000);
		try {
			data = await api.history(chosen, from, to, 600);
			window_ = { from, to };
			error = null;
		} catch (e) {
			error = e;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void range;
		void chosen.length;
		void load();
		const id = setInterval(load, 60_000);
		return () => clearInterval(id);
	});

	const resolution = (s: number) => (s < 3600 ? `${s / 60} min` : s < 86400 ? `${i18n.number(s / 3600, 1)} h` : `${i18n.number(s / 86400, 1)} d`);
	const labelOf = (id: string) => sources.find((s) => s.id === id)?.label ?? id;
</script>

<h1 class="mb-2 text-2xl font-semibold">{t('hist.title')}</h1>
<p class="text-muted-foreground mb-4 text-sm">{t('hist.note')}</p>

<div class="mb-4 flex flex-wrap gap-6">
	<fieldset>
		<legend class="mb-1 text-sm font-medium">{t('hist.range')}</legend>
		<div class="flex flex-wrap gap-3">
			{#each Object.keys(ranges) as r (r)}
				<label class="inline-flex items-center gap-1 text-sm">
					<input type="radio" name="range" value={r} bind:group={range} />{t(`hist.range.${r}` as MessageKey)}
				</label>
			{/each}
		</div>
	</fieldset>
	<fieldset>
		<legend class="mb-1 text-sm font-medium">{t('hist.sources')}</legend>
		<div class="flex flex-wrap gap-3">
			{#each sources as s (s.id)}
				<label class="inline-flex items-center gap-1 text-sm">
					<input type="checkbox" value={s.id} bind:group={chosen} />{s.label}
				</label>
			{/each}
		</div>
	</fieldset>
</div>

<ErrorAlert {error} />
{#if loading && !data}<p class="text-muted-foreground">{t('app.loading')}</p>{/if}

{#if data && window_}
	<p class="text-muted-foreground mb-2 text-xs">{t('hist.resolution', { n: resolution(data.bucket_s) })}</p>
	<div class="grid gap-4">
		{#each data.series as s (s.source)}
			<Card.Root data-testid="history-{s.source}">
				<Card.Header><Card.Title>{labelOf(s.source)} ({s.unit === 'rpm' ? 'RPM' : s.unit})</Card.Title></Card.Header>
				<Card.Content>
					{#if s.points.length}
						<HistoryChart series={s} from={window_.from} to={window_.to} bucketS={data.bucket_s} />
					{:else}
						<p class="text-muted-foreground text-sm">{t('hist.empty')}</p>
					{/if}
				</Card.Content>
			</Card.Root>
		{/each}
	</div>
{/if}
