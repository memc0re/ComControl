<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import StatusBadge from '#lib/components/StatusBadge.svelte';
	import { api, type DeviceInfo } from '#lib/api.ts';
	import { app } from '#lib/app.svelte.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let current = $state('');
	let next = $state('');
	let repeat = $state('');
	let pwError = $state<unknown>(null);
	let pwMsg = $state('');

	let device = $state<DeviceInfo | null>(null);
	let candidates = $state<string[]>([]);
	let devError = $state<unknown>(null);
	let devMsg = $state('');

	async function loadDevice() {
		try {
			device = await api.device();
			candidates = (await api.candidates()).usb_paths;
			devError = null;
		} catch (e) {
			devError = e;
		}
	}
	onMount(loadDevice);

	async function changePassword(e: SubmitEvent) {
		e.preventDefault();
		pwError = null;
		pwMsg = '';
		if (next !== repeat) {
			pwError = t('set.mismatch');
			return;
		}
		try {
			await api.changePassword(current, next);
			current = next = repeat = '';
			pwMsg = t('set.changed');
			app.setSession(await api.session());
		} catch (err) {
			pwError = err;
		}
	}

	async function assign(path: string) {
		devError = null;
		devMsg = '';
		try {
			const { revision } = await api.config();
			await api.assign(revision, path);
			devMsg = t('set.assignedOk');
			await loadDevice();
		} catch (err) {
			devError = err;
		}
	}
</script>

<h1 class="mb-4 text-2xl font-semibold">{t('set.title')}</h1>

<div class="grid gap-4 md:grid-cols-2">
	<Card.Root>
		<Card.Header><Card.Title>{t('set.password')}</Card.Title></Card.Header>
		<Card.Content>
			<form class="grid gap-3" onsubmit={changePassword}>
				<input type="text" autocomplete="username" value={app.session?.username ?? 'admin'} class="hidden" readonly />
				<div class="grid gap-1">
					<Label for="pw-current">{t('set.current')}</Label>
					<Input id="pw-current" type="password" autocomplete="current-password" bind:value={current} required />
				</div>
				<div class="grid gap-1">
					<Label for="pw-new">{t('set.new')}</Label>
					<Input id="pw-new" type="password" autocomplete="new-password" minlength={8} bind:value={next} required />
				</div>
				<div class="grid gap-1">
					<Label for="pw-repeat">{t('set.repeat')}</Label>
					<Input id="pw-repeat" type="password" autocomplete="new-password" bind:value={repeat} required />
				</div>
				<ErrorAlert error={pwError} />
				{#if pwMsg}<p class="text-success text-sm" role="status">{pwMsg}</p>{/if}
				<Button type="submit">{t('set.password')}</Button>
			</form>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header><Card.Title>{t('set.device')}</Card.Title></Card.Header>
		<Card.Content class="space-y-2 text-sm">
			{#if device}
				<StatusBadge status={device.connection} />
				{#if device.error_code}<p class="text-destructive">{i18n.error(device.error_code)}</p>{/if}
				<p>{t('set.assigned')}: <strong>{device.assigned_usb_path ?? '–'}</strong></p>
				<p>{t('dash.firmware')}: {device.firmware ?? '–'}{device.bootloader ? ` (Bootloader ${device.bootloader})` : ''}</p>
				{#if !device.simulated}
					<p class="font-medium">{t('set.candidates')}:</p>
					{#if candidates.length}
						<ul class="space-y-1">
							{#each candidates as c (c)}
								<li class="flex items-center gap-2">
									<code>{c}</code>
									{#if c !== device.assigned_usb_path}
										<Button size="sm" variant="outline" onclick={() => assign(c)}>{t('set.assign')}</Button>
									{/if}
								</li>
							{/each}
						</ul>
					{:else}
						<p>{t('set.none')}</p>
					{/if}
					<p class="text-muted-foreground text-xs">{t('set.assignNote')}</p>
				{/if}
			{/if}
			<ErrorAlert error={devError} />
			{#if devMsg}<p role="status">{devMsg}</p>{/if}
		</Card.Content>
	</Card.Root>

	<Card.Root class="md:col-span-2">
		<Card.Header><Card.Title>{t('set.limits')}</Card.Title></Card.Header>
		<Card.Content><p class="text-sm">{t('set.limitsText')}</p></Card.Content>
	</Card.Root>

	<Card.Root class="border-warning md:col-span-2" data-testid="disclaimer">
		<Card.Header><Card.Title>{t('set.disclaimer')}</Card.Title></Card.Header>
		<Card.Content><p class="text-sm">{t('set.disclaimerText')}</p></Card.Content>
	</Card.Root>

	<Card.Root class="md:col-span-2">
		<Card.Header><Card.Title>{t('set.about')}</Card.Title></Card.Header>
		<Card.Content class="text-sm"><p>ComControl · {t('set.license')}</p></Card.Content>
	</Card.Root>
</div>
