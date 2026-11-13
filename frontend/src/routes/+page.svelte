<script lang="ts">
	import { onMount } from 'svelte';
	import Thermometer from '@lucide/svelte/icons/thermometer';
	import Fan from '@lucide/svelte/icons/fan';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import * as Card from '#lib/components/ui/card/index.ts';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import FanSummary from '#lib/components/FanSummary.svelte';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import { api, type Config, type DeviceInfo, type Measurement } from '#lib/api.ts';
	import { app, POLL_MS } from '#lib/app.svelte.ts';
	import { i18n, t, type MessageKey } from '#lib/i18n/index.svelte.ts';

	// The +3.3 V rail is not wired on the controller (reads a few hundred mV);
	// it is hidden in the overview but still recorded in the history.
	const HIDDEN_RAILS = new Set(['rail_3v3']);

	let config = $state<Config | null>(null);
	let device = $state<DeviceInfo | null>(null);
	let error = $state<unknown>(null);
	let showUnused = $state(false);

	async function load() {
		try {
			[config, device] = await Promise.all([api.config(), api.device()]);
			error = null;
		} catch (e) {
			error = e;
		}
	}

	onMount(() => {
		void load();
		const id = setInterval(load, POLL_MS);
		return () => clearInterval(id);
	});

	const live = $derived(app.live);
	const sensors = $derived(live?.sensors.filter((s) => showUnused || s.usage === 'expected' || s.detected) ?? []);
	const fans = $derived(
		live?.fans.filter((f) => showUnused || f.usage === 'expected' || f.detected_mode === 'dc' || f.detected_mode === 'pwm') ?? []
	);
	const noneExpected = $derived(
		live ? !live.sensors.some((s) => s.usage === 'expected') && !live.fans.some((f) => f.usage === 'expected') : false
	);

	function value(m: Measurement, digits: number, unit: string) {
		return m.value === null ? '–' : `${i18n.number(m.value, digits)} ${unit}`;
	}
	const label = (name: string, id: string) => (name ? `${name} (${id})` : id);
</script>

<h1 class="mb-4 text-2xl font-semibold">{t('nav.dashboard')}</h1>

<ErrorAlert {error} />

{#if !live}
	<p class="text-muted-foreground">{t('app.loading')}</p>
{:else}
	<div class="grid gap-4 md:grid-cols-3">
		<Card.Root>
			<Card.Header><Card.Title>{t('dash.device')}</Card.Title></Card.Header>
			<Card.Content class="space-y-1 text-sm">
				<div data-testid="connection"><StatusBadge status={live.connection} /></div>
				{#if live.connection_error}
					<p class="text-destructive" data-testid="connection-error">{i18n.error(live.connection_error)}</p>
				{/if}
				<p>
					{t('dash.dataAge')}:
					<span data-testid="data-age">{live.data_age_s === null ? '–' : t('dash.seconds', { n: i18n.number(live.data_age_s, 0) })}</span>
				</p>
				{#if device}
					<p>{t('dash.firmware')}: {device.firmware ?? '–'}</p>
					<p>{t('dash.usbPath')}: {device.usb_path ?? '–'}</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header><Card.Title>{t('dash.alarms')}</Card.Title></Card.Header>
			<Card.Content class="text-sm">
				{#if live.active_alarms}
					<a href="/alarms" class="text-destructive inline-flex items-center gap-1 font-semibold underline" data-testid="dash-alarms">
						<TriangleAlert class="size-4" aria-hidden="true" />{live.active_alarms}
					</a>
				{:else}
					<p data-testid="dash-alarms">{t('dash.noAlarms')}</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header><Card.Title>{t('dash.rails')}</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 text-sm">
					{#each live.rails.filter((r) => !HIDDEN_RAILS.has(r.id)) as rail (rail.id)}
						<dt class="text-muted-foreground">{t(`rail.${rail.id}` as MessageKey)}</dt>
						<dd>{value(rail.measurement, 2, 'V')}</dd>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
	</div>

	{#if noneExpected}
		<p class="mt-4 rounded-md border p-3 text-sm" data-testid="no-channels">
			{t('dash.noChannels')} <a href="/channels" class="underline">{t('nav.channels')}</a>
		</p>
	{/if}

	<label class="mt-4 flex items-center gap-2 text-sm">
		<input type="checkbox" bind:checked={showUnused} />
		{t('dash.showUnused')}
	</label>

	<h2 class="mt-4 mb-2 text-lg font-semibold">{t('dash.temperatures')}</h2>
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
		{#each sensors as s (s.id)}
			<Card.Root data-testid="sensor-{s.id}">
				<Card.Header>
					<Card.Title class="flex items-center gap-2"><Thermometer class="size-4" aria-hidden="true" />{label(s.name, s.id)}</Card.Title>
				</Card.Header>
				<Card.Content class="space-y-1">
					<p class="text-3xl font-semibold tabular-nums" data-testid="temp-value">{value(s.temperature, 1, '°C')}</p>
					<div class="flex flex-wrap gap-3">
						<StatusBadge status={s.status} />
						{#if s.usage === 'unused'}<StatusBadge status={s.temperature.status} />{/if}
					</div>
					{#if s.usage === 'unused' && s.detected}
						<p class="text-muted-foreground text-xs">{t('dash.detectedUnused')}</p>
					{/if}
					{#if s.temperature.value === null && s.temperature.last_value !== null && s.temperature.last_valid_at}
						<p class="text-muted-foreground text-xs">
							{t('dash.lastValue', { v: `${i18n.number(s.temperature.last_value, 1)} °C`, t: i18n.dateTime(s.temperature.last_valid_at) })}
						</p>
					{/if}
					{#if s.usage === 'expected'}
						<p class="text-xs {s.alarm_max_c === null ? 'text-warning' : 'text-muted-foreground'}">
							{s.alarm_max_c === null ? t('dash.noLimit') : t('dash.limit', { v: `${i18n.number(s.alarm_max_c, 1)} °C` })}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>
		{/each}
	</div>

	<h2 class="mt-6 mb-2 text-lg font-semibold">{t('dash.fans')}</h2>
	<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
		{#each fans as f (f.id)}
			<Card.Root data-testid="fan-{f.id}">
				<Card.Header>
					<Card.Title class="flex items-center gap-2"><Fan class="size-4" aria-hidden="true" />{label(f.name, f.id)}</Card.Title>
				</Card.Header>
				<Card.Content class="space-y-2">
					<p class="text-3xl font-semibold tabular-nums" data-testid="rpm-value">{value(f.rpm, 0, 'RPM')}</p>
					<div class="flex flex-wrap gap-3">
						<StatusBadge status={f.status} />
						{#if f.usage === 'unused'}<StatusBadge status={f.rpm.status} />{/if}
					</div>
					<p class="text-muted-foreground text-xs">
						{t('fan.mode')}:
						{f.detected_mode === 'unknown'
							? t('mode.unknown', { raw: f.detected_mode_raw })
							: f.detected_mode
								? t(`mode.${f.detected_mode}` as MessageKey)
								: '–'}
					</p>
					{#if f.failsafe}
						<p class="text-destructive text-sm font-semibold" data-testid="fan-failsafe">
							<TriangleAlert class="inline size-4" aria-hidden="true" />
							{f.failsafe.delivered
								? t('fan.failsafe', { r: t(`alarm.reason.${f.failsafe.reason}` as MessageKey) })
								: t('fan.failsafeUndelivered')}
						</p>
					{/if}
					{#if f.usage === 'expected'}
						<FanSummary fan={f} config={config?.fans.find((c) => c.id === f.id)} />
					{:else if f.detected_mode === 'dc' || f.detected_mode === 'pwm'}
						<p class="text-muted-foreground text-xs">{t('dash.detectedUnused')}</p>
					{/if}
				</Card.Content>
			</Card.Root>
		{/each}
	</div>
{/if}
