<script lang="ts">
	import './layout.css';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import LogOut from '@lucide/svelte/icons/log-out';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import WifiOff from '@lucide/svelte/icons/wifi-off';
	import Bell from '@lucide/svelte/icons/bell';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { app } from '#lib/app.svelte.ts';
	import { i18n, t, type MessageKey } from '#lib/i18n/index.svelte.ts';
	import { theme } from '#lib/theme.svelte.ts';
	import favicon from '#lib/assets/favicon.svg';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const nav: { href: string; key: MessageKey }[] = [
		{ href: '/', key: 'nav.dashboard' },
		{ href: '/channels', key: 'nav.channels' },
		{ href: '/curves', key: 'nav.curves' },
		{ href: '/history', key: 'nav.history' },
		{ href: '/alarms', key: 'nav.alarms' },
		{ href: '/settings', key: 'nav.settings' }
	];

	onMount(async () => {
		i18n.init();
		theme.init();
		await app.checkSession();
	});

	const onLogin = $derived(page.url.pathname === '/login');

	$effect(() => {
		if (!app.checked) return;
		if (!app.session && !onLogin) void goto('/login');
		if (app.session && onLogin) void goto('/');
	});

	const anyFailsafe = $derived(app.live?.fans.some((f) => f.failsafe) ?? false);
	const undelivered = $derived(app.live?.fans.some((f) => f.failsafe && !f.failsafe.delivered) ?? false);
</script>

<svelte:head>
	<title>{t('app.title')}</title>
	<link rel="icon" href={favicon} />
</svelte:head>

<a href="#main" class="sr-only focus:not-sr-only focus:absolute focus:p-2">{t('app.skip')}</a>

<div class="min-h-dvh">
	{#if app.live?.simulated}
		<div class="bg-warning text-background flex items-center justify-center gap-2 px-3 py-1 text-sm font-semibold" role="status" data-testid="simulation-banner">
			<FlaskConical class="size-4" aria-hidden="true" />
			{t('app.simulation')}
		</div>
	{/if}

	<header class="border-b">
		<div class="mx-auto flex max-w-6xl flex-wrap items-center gap-2 px-4 py-2">
			<a href="/" class="mr-2 text-lg font-semibold">{t('app.title')}</a>
			{#if app.session}
				<nav aria-label={t('nav.menu')} class="order-last flex w-full flex-wrap gap-1 sm:order-none sm:w-auto">
					{#each nav as item (item.href)}
						{@const active = page.url.pathname === item.href}
						<a
							href={item.href}
							aria-current={active ? 'page' : undefined}
							class="hover:bg-muted rounded-md px-2 py-1 text-sm {active ? 'bg-muted font-semibold' : ''}"
						>
							{t(item.key)}
							{#if item.href === '/alarms' && app.live?.active_alarms}
								<span class="text-destructive inline-flex items-center gap-0.5" data-testid="nav-alarm-count">
									<Bell class="size-3" aria-hidden="true" />{app.live.active_alarms}
								</span>
							{/if}
						</a>
					{/each}
				</nav>
			{/if}
			<div class="ml-auto flex items-center gap-1">
				<label class="sr-only" for="lang">{t('lang.label')}</label>
				<select
					id="lang"
					class="bg-background h-8 rounded-md border px-1 text-sm"
					value={i18n.lang}
					onchange={(e) => i18n.set((e.currentTarget as HTMLSelectElement).value as 'de' | 'en')}
					data-testid="lang-switch"
				>
					<option value="de">Deutsch</option>
					<option value="en">English</option>
				</select>
				<Button
					variant="ghost"
					size="icon"
					onclick={() => theme.toggle()}
					aria-label={t('theme.toggle')}
					title={theme.current === 'dark' ? t('theme.light') : t('theme.dark')}
					data-testid="theme-toggle"
				>
					{#if theme.current === 'dark'}<Sun />{:else}<Moon />{/if}
				</Button>
				{#if app.session}
					<Button variant="ghost" size="sm" onclick={() => app.logout()} data-testid="logout">
						<LogOut />
						<span class="hidden sm:inline">{t('nav.logout')}</span>
					</Button>
				{/if}
			</div>
		</div>
	</header>

	<div class="mx-auto max-w-6xl space-y-2 px-4 pt-3" aria-live="polite">
		{#if app.backendLost}
			<p class="border-destructive text-destructive flex items-center gap-2 rounded-md border px-3 py-2 text-sm" role="alert" data-testid="backend-lost">
				<WifiOff class="size-4" aria-hidden="true" />{t('app.backendLost')}
			</p>
		{/if}
		{#if app.session?.password_is_default && !onLogin}
			<p class="border-warning text-warning flex items-center gap-2 rounded-md border px-3 py-2 text-sm" role="status">
				<TriangleAlert class="size-4" aria-hidden="true" />
				<a href="/settings" class="underline">{t('app.defaultPassword')}</a>
			</p>
		{/if}
		{#if anyFailsafe}
			<p class="border-destructive text-destructive flex items-center gap-2 rounded-md border px-3 py-2 text-sm" role="alert" data-testid="failsafe-banner">
				<TriangleAlert class="size-4" aria-hidden="true" />
				<a href="/alarms" class="underline">
					{undelivered ? t('fan.failsafeUndelivered') : t('alarm.failsafe')}
				</a>
			</p>
		{/if}
	</div>

	<main id="main" class="mx-auto max-w-6xl px-4 py-4">
		{#if !app.checked}
			<p class="text-muted-foreground">{t('app.loading')}</p>
		{:else if app.session || onLogin}
			{@render children()}
		{/if}
	</main>
</div>
