<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import * as Alert from '#lib/components/ui/alert/index.ts';
	import { ApiError } from '#lib/api.ts';
	import { i18n } from '#lib/i18n/index.svelte.ts';

	let { error }: { error: unknown } = $props();

	const message = $derived.by(() => {
		if (!error) return '';
		if (error instanceof ApiError) {
			if (error.code === 'apply_failed') {
				const key = error.details.uncertain ? 'apply_failed_uncertain' : 'apply_failed';
				return i18n.error(key, {
					...error.details,
					error: i18n.error(String(error.details.error ?? ''))
				});
			}
			return i18n.error(error.code, error.details);
		}
		return typeof error === 'string' ? error : String(error);
	});
</script>

{#if error}
	<Alert.Root variant="destructive" role="alert" data-testid="error-alert">
		<CircleAlert aria-hidden="true" />
		<Alert.Description>{message}</Alert.Description>
	</Alert.Root>
{/if}
