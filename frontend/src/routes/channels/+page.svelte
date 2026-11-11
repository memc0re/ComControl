<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import { api, type Config, type FanConfig, type SensorConfig } from '#lib/api.ts';
	import { app } from '#lib/app.svelte.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let config = $state<Config | null>(null);
	let loadError = $state<unknown>(null);
	// per-channel editable copies, errors and messages
	let fanEdit = $state<Record<string, { name: string; usage: string; max_rpm: string; dc_start_percent: string }>>({});
	let sensorEdit = $state<Record<string, { name: string; usage: string; alarm_max_c: string }>>({});
	let errors = $state<Record<string, unknown>>({});
	let messages = $state<Record<string, string>>({});
	let busy = $state<string | null>(null);

	function reset(c: Config) {
		config = c;
		fanEdit = Object.fromEntries(
			c.fans.map((f: FanConfig) => [f.id, {
				name: f.name, usage: f.usage,
				max_rpm: f.max_rpm === null ? '' : String(f.max_rpm),
				dc_start_percent: String(f.dc_start_percent)
			}])
		);
		sensorEdit = Object.fromEntries(
			c.sensors.map((s: SensorConfig) => [s.id, {
				name: s.name, usage: s.usage, alarm_max_c: s.alarm_max_c === null ? '' : String(s.alarm_max_c)
			}])
		);
	}

	async function load() {
		try {
			reset(await api.config());
			loadError = null;
		} catch (e) {
			loadError = e;
		}
	}
	onMount(load);

	const num = (v: string) => (v.trim() === '' ? null : Number(v.replace(',', '.')));

	async function saveFan(id: string) {
		if (!config) return;
		const e = fanEdit[id];
		busy = id;
		errors[id] = null;
		messages[id] = '';
		try {
			const res = await api.updateFan(id, {
				revision: config.revision, name: e.name, usage: e.usage,
				max_rpm: num(e.max_rpm), dc_start_percent: num(e.dc_start_percent) ?? 50
			});
			// an outcome is only returned when the save activated the fan (100 % sent)
			const msg = res.outcome
				? res.outcome.ok
					? t('ch.activationSent')
					: t('ch.activationNotSent', { e: i18n.error(res.outcome.error ?? '') })
				: t('ch.saved');
			reset(await api.config());
			messages[id] = msg;
			void app.refresh();
		} catch (err) {
			errors[id] = err;
		} finally {
			busy = null;
		}
	}

	async function saveSensor(id: string) {
		if (!config) return;
		const e = sensorEdit[id];
		busy = id;
		errors[id] = null;
		messages[id] = '';
		try {
			await api.updateSensor(id, {
				revision: config.revision, name: e.name, usage: e.usage, alarm_max_c: num(e.alarm_max_c)
			});
			reset(await api.config());
			messages[id] = t('ch.saved');
			void app.refresh();
		} catch (err) {
			errors[id] = err;
		} finally {
			busy = null;
		}
	}

	const liveFan = (id: string) => app.live?.fans.find((f) => f.id === id);
	const liveSensor = (id: string) => app.live?.sensors.find((s) => s.id === id);
</script>

<h1 class="mb-2 text-2xl font-semibold">{t('ch.title')}</h1>
<p class="text-muted-foreground mb-4 text-sm">{t('ch.intro')}</p>

<ErrorAlert error={loadError} />

{#if !config}
	<p class="text-muted-foreground">{t('app.loading')}</p>
{:else}
	<h2 class="mb-2 text-lg font-semibold">{t('ch.fans')}</h2>
	<p class="text-muted-foreground mb-2 text-sm">{t('ch.activateNote')}</p>
	<div class="grid gap-3 md:grid-cols-2">
		{#each config.fans as f (f.id)}
			{@const e = fanEdit[f.id]}
			{@const lf = liveFan(f.id)}
			<Card.Root data-testid="ch-{f.id}">
				<Card.Header>
					<Card.Title>{f.id}</Card.Title>
					<Card.Description class="flex flex-wrap gap-3">
						{#if lf}
							<StatusBadge status={lf.status} />
							<span>{lf.detected_mode === 'dc' || lf.detected_mode === 'pwm' ? `${t('ch.detected')}: ${lf.detected_mode.toUpperCase()}` : t('ch.notDetected')}</span>
						{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<form class="grid gap-3" onsubmit={(ev) => { ev.preventDefault(); void saveFan(f.id); }}>
						<div class="grid gap-1">
							<Label for="{f.id}-name">{t('ch.name')}</Label>
							<Input id="{f.id}-name" maxlength={64} bind:value={e.name} />
						</div>
						<div class="grid gap-1">
							<Label for="{f.id}-usage">{t('ch.usage')}</Label>
							<select id="{f.id}-usage" class="bg-background h-9 rounded-md border px-2" bind:value={e.usage}>
								<option value="unused">{t('ch.usage.unused')}</option>
								<option value="expected">{t('ch.usage.expected')}</option>
							</select>
						</div>
						<div class="grid gap-1">
							<Label for="{f.id}-max">{t('ch.maxRpm')}</Label>
							<Input id="{f.id}-max" inputmode="numeric" bind:value={e.max_rpm} aria-describedby="{f.id}-max-hint" />
							<p id="{f.id}-max-hint" class="text-muted-foreground text-xs">{t('ch.maxRpmHint')}</p>
						</div>
						<div class="grid gap-1">
							<Label for="{f.id}-dc">{t('ch.dcStart')}</Label>
							<Input id="{f.id}-dc" inputmode="numeric" bind:value={e.dc_start_percent} aria-describedby="{f.id}-dc-hint" />
							<p id="{f.id}-dc-hint" class="text-muted-foreground text-xs">{t('ch.dcStartHint')}</p>
						</div>
						<ErrorAlert error={errors[f.id]} />
						{#if messages[f.id]}<p class="text-sm" role="status">{messages[f.id]}</p>{/if}
						<Button type="submit" disabled={busy !== null}>{t('ch.save')}</Button>
					</form>
				</Card.Content>
			</Card.Root>
		{/each}
	</div>

	<h2 class="mt-6 mb-2 text-lg font-semibold">{t('ch.sensors')}</h2>
	<div class="grid gap-3 md:grid-cols-2">
		{#each config.sensors as s (s.id)}
			{@const e = sensorEdit[s.id]}
			{@const ls = liveSensor(s.id)}
			<Card.Root data-testid="ch-{s.id}">
				<Card.Header>
					<Card.Title>{s.id}</Card.Title>
					<Card.Description class="flex flex-wrap gap-3">
						{#if ls}
							<StatusBadge status={ls.status} />
							<span>{ls.detected ? t('ch.detected') : t('ch.notDetected')}</span>
						{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<form class="grid gap-3" onsubmit={(ev) => { ev.preventDefault(); void saveSensor(s.id); }}>
						<div class="grid gap-1">
							<Label for="{s.id}-name">{t('ch.name')}</Label>
							<Input id="{s.id}-name" maxlength={64} bind:value={e.name} />
						</div>
						<div class="grid gap-1">
							<Label for="{s.id}-usage">{t('ch.usage')}</Label>
							<select id="{s.id}-usage" class="bg-background h-9 rounded-md border px-2" bind:value={e.usage}>
								<option value="unused">{t('ch.usage.unused')}</option>
								<option value="expected">{t('ch.usage.expected')}</option>
							</select>
						</div>
						<div class="grid gap-1">
							<Label for="{s.id}-limit">{t('ch.alarmMax')}</Label>
							<Input id="{s.id}-limit" inputmode="decimal" bind:value={e.alarm_max_c} aria-describedby="{s.id}-limit-hint" />
							<p id="{s.id}-limit-hint" class="text-muted-foreground text-xs">{t('ch.alarmMaxHint')}</p>
						</div>
						<ErrorAlert error={errors[s.id]} />
						{#if messages[s.id]}<p class="text-sm" role="status">{messages[s.id]}</p>{/if}
						<Button type="submit" disabled={busy !== null}>{t('ch.save')}</Button>
					</form>
				</Card.Content>
			</Card.Root>
		{/each}
	</div>
{/if}
