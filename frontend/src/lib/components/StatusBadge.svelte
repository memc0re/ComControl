<script lang="ts">
	// Status shown as icon + text (never by colour alone).
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Clock from '@lucide/svelte/icons/clock';
	import CircleMinus from '@lucide/svelte/icons/circle-minus';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import { t, type MessageKey } from '#lib/i18n/index.svelte.ts';

	let { status }: { status: string } = $props();

	const kinds: Record<string, { icon: typeof CircleCheck; cls: string }> = {
		ok: { icon: CircleCheck, cls: 'text-success' },
		present: { icon: CircleCheck, cls: 'text-success' },
		connected: { icon: CircleCheck, cls: 'text-success' },
		not_connected: { icon: CircleMinus, cls: 'text-muted-foreground' },
		unused: { icon: CircleMinus, cls: 'text-muted-foreground' },
		no_data: { icon: CircleDashed, cls: 'text-muted-foreground' },
		stale: { icon: Clock, cls: 'text-warning' },
		expected_missing: { icon: CircleAlert, cls: 'text-destructive' },
		error: { icon: CircleX, cls: 'text-destructive' },
		disconnected: { icon: CircleX, cls: 'text-destructive' }
	};
	const kind = $derived(kinds[status] ?? kinds.error);
	const Icon = $derived(kind.icon);
</script>

<span class="inline-flex items-center gap-1 text-sm font-medium {kind.cls}" data-status={status}>
	<Icon class="size-4 shrink-0" aria-hidden="true" />
	{t(`status.${status}` as MessageKey)}
</span>
