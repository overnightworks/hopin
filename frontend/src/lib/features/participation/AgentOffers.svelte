<script lang="ts">
    import { AgentControl } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let { view, onCall }: { view: AgentOffersView; onCall: (key: string) => void } = $props();
</script>

{#if view.shown}
    <AgentControl rows={view.rows} {onCall} onSendAway={() => undefined} refusal={view.refusal} />
{/if}
