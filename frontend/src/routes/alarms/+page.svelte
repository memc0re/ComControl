<script lang="ts">
	import { onMount } from 'svelte';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import { api, type Alarm, type EventEntry } from '#lib/api.ts';
	import { app, POLL_MS } from '#lib/app.svelte.ts';
	import { i18n, t, type MessageKey } from '#lib/i18n/index.svelte.ts';

	let alarms = $state<Alarm[] | null>(null);
	let events = $state<EventEntry[]>([]);
	let error = $state<unknown>(null);
	let releaseError = $state<unknown>(null);
	let releaseMsg = $state('');
	let busy = $state(false);

	async function load() {
		try {
			const [a, e] = await Promise.all([api.alarms('all'), api.events(50)]);
			alarms = a.alarms;
			events = e.events;
			error = null;
		} catch (err) {
			error = err;
		}
	}
	onMount(() => {
		void load();
		const id = setInterval(load, POLL_MS);
		return () => clearInterval(id);
	});

	const active = $derived(alarms?.filter((a) => a.active) ?? []);
	const past = $derived(alarms?.filter((a) => !a.active) ?? []);
	const latched = $derived(app.live?.fans.filter((f) => f.failsafe) ?? []);

	async function ack(id: number) {
		try {
			await api.acknowledge(id);
			await load();
			void app.refresh();
		} catch (err) {
			error = err;
		}
	}

	async function release() {
		busy = true;
		releaseError = null;
		releaseMsg = '';
		try {
			const r = await api.release();
			releaseMsg = [
				r.released.length ? t('alarm.released', { list: r.released.join(', ') }) : '',
				r.blocked.length ? t('alarm.blocked', { list: r.blocked.join(', ') }) : ''
			].filter(Boolean).join(' ');
			await app.refresh();
			await load();
		} catch (err) {
			releaseError = err;
		} finally {
			busy = false;
		}
	}

	const typeName = (a: Alarm) => t(`alarm.type.${a.type}` as MessageKey);
	function detail(a: Alarm): string {
		const d = a.details;
		if (a.type === 'over_temperature' && typeof d.value === 'number')
			return `${i18n.number(d.value, 1)} °C > ${i18n.number(Number(d.limit), 1)} °C`;
		if (a.type === 'device_error' && d.code) return i18n.error(String(d.code));
		if (a.type === 'sensor_failure' && d.status) return t(`status.${d.status}` as MessageKey);
		return '';
	}
</script>

<h1 class="mb-4 text-2xl font-semibold">{t('alarm.title')}</h1>
<ErrorAlert {error} />

<Card.Root class="mb-4">
	<Card.Header><Card.Title>{t('alarm.failsafe')}</Card.Title></Card.Header>
	<Card.Content class="space-y-3 text-sm">
		{#if !latched.length}
			<p data-testid="failsafe-none"><CircleCheck class="text-success inline size-4" aria-hidden="true" /> {t('alarm.failsafeNone')}</p>
		{:else}
			<p>{t('alarm.failsafeText')}</p>
			<ul class="list-disc pl-5" data-testid="failsafe-list">
				{#each latched as f (f.id)}
					<li>
						<strong>{f.name ? `${f.name} (${f.id})` : f.id}</strong>:
						{t(`alarm.reason.${f.failsafe!.reason}` as MessageKey)} ({f.failsafe!.source}),
						{t('alarm.since', { t: i18n.dateTime(f.failsafe!.since) })}
						{#if !f.failsafe!.delivered}
							<span class="text-destructive font-semibold">– {t('fan.failsafeUndelivered')}{f.failsafe!.last_error ? ` (${i18n.error(f.failsafe!.last_error)})` : ''}</span>
						{/if}
					</li>
				{/each}
			</ul>
			<ErrorAlert error={releaseError} />
			<Button onclick={release} disabled={busy} data-testid="release">{t('alarm.release')}</Button>
		{/if}
		{#if releaseMsg}<p role="status" data-testid="release-msg">{releaseMsg}</p>{/if}
	</Card.Content>
</Card.Root>

<h2 class="mb-2 text-lg font-semibold">{t('alarm.active')}</h2>
<p class="text-muted-foreground mb-2 text-xs">{t('alarm.ackNote')}</p>
{#if alarms === null}
	<p class="text-muted-foreground">{t('app.loading')}</p>
{:else if !active.length}
	<p class="text-sm" data-testid="no-active-alarms">{t('alarm.none')}</p>
{:else}
	<ul class="space-y-2" data-testid="active-alarms">
		{#each active as a (a.id)}
			<li class="border-destructive flex flex-wrap items-center gap-3 rounded-md border p-3 text-sm">
				<TriangleAlert class="text-destructive size-4" aria-hidden="true" />
				<strong>{typeName(a)}</strong>
				<span>{a.source}</span>
				<span>{detail(a)}</span>
				<span class="text-muted-foreground">{t('alarm.since', { t: i18n.dateTime(a.started_at) })}</span>
				{#if a.acknowledged_at}
					<span class="text-muted-foreground">{t('alarm.acked', { t: i18n.dateTime(a.acknowledged_at) })}</span>
				{:else}
					<Button size="sm" variant="outline" class="ml-auto" onclick={() => ack(a.id)}>{t('alarm.ack')}</Button>
				{/if}
			</li>
		{/each}
	</ul>
{/if}

<h2 class="mt-6 mb-2 text-lg font-semibold">{t('alarm.history')}</h2>
{#if past.length}
	<div class="overflow-x-auto">
		<table class="w-full text-sm">
			<tbody>
				{#each past as a (a.id)}
					<tr class="border-b">
						<td class="py-1 pr-3">{typeName(a)}</td>
						<td class="pr-3">{a.source}</td>
						<td class="pr-3">{detail(a)}</td>
						<td class="pr-3">{i18n.dateTime(a.started_at)}</td>
						<td>{t('alarm.ended', { t: i18n.dateTime(a.ended_at) })}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{:else}
	<p class="text-sm">{t('alarm.none')}</p>
{/if}

<h2 class="mt-6 mb-2 text-lg font-semibold">{t('alarm.events')}</h2>
<div class="overflow-x-auto">
	<table class="w-full text-xs">
		<tbody>
			{#each events as e (e.id)}
				<tr class="border-b">
					<td class="py-1 pr-3 whitespace-nowrap">{i18n.dateTime(e.at)}</td>
					<td class="pr-3 font-mono">{e.kind}</td>
					<td class="font-mono break-all">{Object.keys(e.details).length ? JSON.stringify(e.details) : ''}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
