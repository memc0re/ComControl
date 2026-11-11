<script lang="ts">
	// Desired configuration, last transmitted command and measured speed are
	// shown separately: a transmitted command is not a confirmed state.
	import type { FanConfig, FanState } from '#lib/api.ts';
	import { i18n, t } from '#lib/i18n/index.svelte.ts';

	let { fan, config }: { fan: FanState; config: FanConfig | undefined } = $props();

	function describe(c: Record<string, unknown> | FanConfig | undefined): string {
		if (!c) return '–';
		const type = 'control_type' in c ? c.control_type : c.type;
		if (type === 'fixed') return t('fan.fixed', { p: 'percent' in c ? c.percent : '?' });
		if (type === 'curve') {
			const s = 'curve_sensor' in c ? c.curve_sensor : `temp${c.sensor}`;
			return t('fan.curve', { s: String(s ?? '?') });
		}
		return '–';
	}

	const cmd = $derived(fan.last_command);
</script>

<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
	<dt class="text-muted-foreground">{t('fan.desired')}</dt>
	<dd data-testid="fan-desired">{describe(config)}</dd>
	<dt class="text-muted-foreground">{t('fan.sent')}</dt>
	<dd data-testid="fan-sent">
		{#if cmd}
			{describe(cmd.command)} –
			{cmd.ok ? t('fan.sentOk') : t('fan.sentFailed', { e: i18n.error(cmd.error ?? '') })}
			<span class="text-muted-foreground block text-xs">{i18n.dateTime(cmd.sent_at)}</span>
			{#if !cmd.ok && cmd.uncertain}
				<span class="text-destructive block">{t('fan.uncertain')}</span>
			{/if}
		{:else}
			{t('fan.notSent')}
		{/if}
	</dd>
	<dt class="text-muted-foreground">{t('fan.measured')}</dt>
	<dd>
		{fan.rpm.value !== null ? `${i18n.number(fan.rpm.value)} RPM` : '–'}
	</dd>
</dl>
