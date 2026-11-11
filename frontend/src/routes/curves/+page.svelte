<script lang="ts">
	import { onMount } from 'svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import CurveChart from '#lib/components/CurveChart.svelte';
	import FanSummary from '#lib/components/FanSummary.svelte';
	import { api, ApiError, type Config, type CurvePoint, type FanConfig } from '#lib/api.ts';
	import { app } from '#lib/app.svelte.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let config = $state<Config | null>(null);
	let loadError = $state<unknown>(null);
	let selected = $state<string | null>(null);

	// editor state (strings so that partial input stays editable)
	let fanMode = $state<'auto' | 'dc' | 'pwm'>('auto');
	let controlType = $state<'fixed' | 'curve'>('fixed');
	let percent = $state('100');
	let sensor = $state('');
	let points = $state<{ t: string; r: string }[]>([]);
	let effective = $state<CurvePoint[] | null>(null);
	let curveError = $state<unknown>(null);
	let applyError = $state<unknown>(null);
	let message = $state('');
	let busy = $state(false);

	const expectedFans = $derived(config?.fans.filter((f) => f.usage === 'expected') ?? []);
	const fan = $derived(expectedFans.find((f) => f.id === selected) ?? null);
	const liveFan = $derived(app.live?.fans.find((f) => f.id === selected));
	const usableSensors = $derived(
		config?.sensors.filter(
			(s) => s.usage === 'expected' && app.live?.sensors.find((l) => l.id === s.id)?.temperature.status === 'ok'
		) ?? []
	);
	const currentTemp = $derived(app.live?.sensors.find((s) => s.id === sensor)?.temperature.value ?? null);
	const locked = $derived(!!liveFan?.failsafe);

	function edit(f: FanConfig) {
		selected = f.id;
		fanMode = f.fan_mode;
		controlType = f.control_type;
		percent = String(f.percent);
		sensor = f.curve_sensor ?? '';
		points = (f.curve_points ?? (f.max_rpm ? [{ temperature_c: 25, rpm: Math.round(f.max_rpm / 2) }, { temperature_c: 40, rpm: f.max_rpm }] : [])).map(
			(p) => ({ t: String(p.temperature_c), r: String(p.rpm) })
		);
		applyError = null;
		message = '';
	}

	async function load(keepSelection = true) {
		try {
			config = await api.config();
			loadError = null;
			const current = config.fans.find((f) => f.id === selected && f.usage === 'expected');
			if (!keepSelection || !current) {
				const first = config.fans.find((f) => f.usage === 'expected');
				if (first) edit(first);
			} else edit(current);
		} catch (e) {
			loadError = e;
		}
	}
	onMount(() => void load(false));

	const parsed = $derived(
		points.map((p) => ({ temperature_c: Number(p.t.replace(',', '.')), rpm: Number(p.r) }))
	);

	// validate the curve on the server (same rules as on apply) and show the effective form
	let checkSeq = 0;
	$effect(() => {
		if (controlType !== 'curve' || !fan) return;
		const pts = $state.snapshot(parsed);
		const max = fan.max_rpm;
		const seq = ++checkSeq;
		const timer = setTimeout(async () => {
			if (pts.some((p) => !Number.isFinite(p.temperature_c) || !Number.isFinite(p.rpm)) || !pts.length) {
				effective = null;
				curveError = new ApiError(422, 'curve_value_not_finite');
				return;
			}
			try {
				const res = await api.checkCurve(max, pts);
				if (seq === checkSeq) {
					effective = res.effective_curve;
					curveError = null;
				}
			} catch (e) {
				if (seq === checkSeq) {
					effective = null;
					curveError = e;
				}
			}
		}, 300);
		return () => clearTimeout(timer);
	});

	async function apply() {
		if (!config || !fan) return;
		busy = true;
		applyError = null;
		message = '';
		const body: Record<string, unknown> = { revision: config.revision, fan_mode: fanMode, control_type: controlType };
		if (controlType === 'fixed') body.percent = Number(percent);
		else {
			body.curve_sensor = sensor || null;
			body.curve_points = $state.snapshot(parsed);
		}
		try {
			await api.applyFan(fan.id, body);
			message = t('ctl.applied');
			await load();
			message = t('ctl.applied');
			void app.refresh();
		} catch (e) {
			applyError = e;
		} finally {
			busy = false;
		}
	}
</script>

<h1 class="mb-2 text-2xl font-semibold">{t('ctl.title')}</h1>
<p class="text-muted-foreground mb-4 text-sm">{t('ctl.intro')}</p>

<ErrorAlert error={loadError} />

{#if !config}
	<p class="text-muted-foreground">{t('app.loading')}</p>
{:else if !expectedFans.length}
	<p class="rounded-md border p-3 text-sm" data-testid="no-fans">{t('ctl.noFans')} <a class="underline" href="/channels">{t('nav.channels')}</a></p>
{:else}
	<div class="mb-4 flex flex-wrap gap-2" role="group" aria-label={t('dash.fans')}>
		{#each expectedFans as f (f.id)}
			<Button variant={f.id === selected ? 'default' : 'outline'} onclick={() => edit(f)} aria-pressed={f.id === selected}>
				{f.name ? `${f.name} (${f.id})` : f.id}
			</Button>
		{/each}
	</div>

	{#if fan}
		<div class="grid gap-4 lg:grid-cols-[2fr_1fr]">
			<Card.Root>
				<Card.Content class="space-y-4 pt-4">
					{#if locked}
						<p class="text-destructive flex items-center gap-2 text-sm font-semibold" role="alert" data-testid="ctl-locked">
							<TriangleAlert class="size-4" aria-hidden="true" />{t('ctl.failsafeLocked')}
							<a class="underline" href="/alarms">{t('nav.alarms')}</a>
						</p>
					{/if}
					<form class="space-y-4" onsubmit={(e) => { e.preventDefault(); void apply(); }}>
						<div class="grid gap-1">
							<Label for="fan-mode">{t('ctl.fanMode')}</Label>
							<select id="fan-mode" class="bg-background h-9 rounded-md border px-2" bind:value={fanMode}>
								<option value="auto">{t('mode.auto')}</option>
								<option value="pwm">PWM</option>
								<option value="dc">DC</option>
							</select>
							{#if fanMode !== 'auto' && liveFan?.detected_mode && fanMode !== liveFan.detected_mode}
								<p class="text-warning text-xs">{t('ctl.fanModeNote')}</p>
							{/if}
						</div>

						<fieldset class="space-y-1">
							<legend class="text-sm font-medium">{t('ctl.type')}</legend>
							<label class="mr-4 inline-flex items-center gap-2 text-sm">
								<input type="radio" name="type" value="fixed" bind:group={controlType} /> {t('ctl.type.fixed')}
							</label>
							<label class="inline-flex items-center gap-2 text-sm">
								<input type="radio" name="type" value="curve" bind:group={controlType} /> {t('ctl.type.curve')}
							</label>
						</fieldset>

						{#if controlType === 'fixed'}
							<div class="grid gap-1">
								<Label for="percent">{t('ctl.percent')}</Label>
								<div class="flex items-center gap-3">
									<input type="range" min="0" max="100" step="1" bind:value={percent} class="flex-1" aria-label={t('ctl.percent')} />
									<Input id="percent" class="w-20" inputmode="numeric" bind:value={percent} />
								</div>
								<p class="text-muted-foreground text-xs">{t('ctl.percentNote')}</p>
							</div>
						{:else}
							{#if !fan.max_rpm}
								<p class="text-warning text-sm" role="status">{t('ctl.maxRpmMissing')}</p>
							{/if}
							{#if !usableSensors.length}
								<p class="text-warning text-sm" role="status">{t('ctl.noSensor')}</p>
							{/if}
							<div class="grid gap-1">
								<Label for="sensor">{t('ctl.sensor')}</Label>
								<select id="sensor" class="bg-background h-9 rounded-md border px-2" bind:value={sensor}>
									<option value="">–</option>
									{#each usableSensors as s (s.id)}
										<option value={s.id}>{s.name ? `${s.name} (${s.id})` : s.id}</option>
									{/each}
								</select>
							</div>
							<fieldset class="space-y-2">
								<legend class="text-sm font-medium">{t('ctl.points')}</legend>
								{#each points as p, i (i)}
									<div class="flex items-end gap-2" data-testid="curve-point">
										<div class="grid gap-1">
											<Label for="pt-{i}-t" class="text-xs">{t('ctl.temp')}</Label>
											<Input id="pt-{i}-t" class="w-28" inputmode="decimal" bind:value={p.t} />
										</div>
										<div class="grid gap-1">
											<Label for="pt-{i}-r" class="text-xs">{t('ctl.rpm')}</Label>
											<Input id="pt-{i}-r" class="w-28" inputmode="numeric" bind:value={p.r} />
										</div>
										<Button variant="ghost" size="icon" aria-label={t('ctl.removePoint', { n: i + 1 })} disabled={points.length <= 1} onclick={() => points.splice(i, 1)}>
											<Trash />
										</Button>
									</div>
								{/each}
								<Button variant="outline" size="sm" disabled={points.length >= 6} onclick={() => points.push({ t: '', r: fan?.max_rpm ? String(fan.max_rpm) : '' })}>
									<Plus />{t('ctl.addPoint')}
								</Button>
								{#if fan.max_rpm}<p class="text-muted-foreground text-xs">{t('ctl.lastMax', { v: fan.max_rpm })}</p>{/if}
							</fieldset>
							<ErrorAlert error={curveError} />
						{/if}

						<ErrorAlert error={applyError} />
						{#if message}<p class="text-success text-sm" role="status" data-testid="apply-ok">{message}</p>{/if}
						<div class="flex gap-2">
							<Button type="submit" disabled={busy || locked} data-testid="apply">{t('ctl.apply')}</Button>
							<Button variant="outline" onclick={() => fan && edit(fan)}>{t('ctl.reset')}</Button>
						</div>
					</form>
				</Card.Content>
			</Card.Root>

			<div class="space-y-4">
				{#if liveFan}
					<Card.Root>
						<Card.Content class="pt-4"><FanSummary fan={liveFan} config={fan} /></Card.Content>
					</Card.Root>
				{/if}
				{#if controlType === 'curve' && effective}
					<Card.Root>
						<Card.Header><Card.Title>{t('ctl.effective')}</Card.Title></Card.Header>
						<Card.Content class="space-y-2">
							<CurveChart points={effective} {currentTemp} />
							<table class="w-full text-sm" data-testid="effective-curve">
								<thead><tr><th class="text-left">{t('ctl.temp')}</th><th class="text-right">{t('ctl.rpm')}</th></tr></thead>
								<tbody>
									{#each effective as p, i (i)}
										<tr><td>{i18n.number(p.temperature_c, 2)}</td><td class="text-right">{i18n.number(p.rpm)}</td></tr>
									{/each}
								</tbody>
							</table>
							<p class="text-muted-foreground text-xs">{t('ctl.effectiveNote')}</p>
						</Card.Content>
					</Card.Root>
				{/if}
			</div>
		</div>
	{/if}
{/if}
