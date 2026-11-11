<script lang="ts">
	import { goto } from '$app/navigation';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import ErrorAlert from '#lib/components/ErrorAlert.svelte';
	import { api } from '#lib/api.ts';
	import { app } from '#lib/app.svelte.ts';
	import { t } from '#lib/i18n/index.svelte.ts';

	let username = $state('admin');
	let password = $state('');
	let error = $state<unknown>(null);
	let busy = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		error = null;
		try {
			app.setSession(await api.login(username, password));
			password = '';
			await goto('/');
		} catch (err) {
			error = err;
		} finally {
			busy = false;
		}
	}
</script>

<Card.Root class="mx-auto mt-8 max-w-sm">
	<Card.Header>
		<Card.Title><h1>{t('login.title')}</h1></Card.Title>
		<Card.Description>{t('login.hint')}</Card.Description>
	</Card.Header>
	<Card.Content>
		<form class="space-y-4" onsubmit={submit}>
			<div class="space-y-1">
				<Label for="username">{t('login.username')}</Label>
				<Input id="username" autocomplete="username" bind:value={username} required />
			</div>
			<div class="space-y-1">
				<Label for="password">{t('login.password')}</Label>
				<Input id="password" type="password" autocomplete="current-password" bind:value={password} required />
			</div>
			<ErrorAlert {error} />
			<Button type="submit" class="w-full" disabled={busy}>{t('login.submit')}</Button>
		</form>
	</Card.Content>
</Card.Root>
