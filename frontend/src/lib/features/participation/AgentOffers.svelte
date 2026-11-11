<script lang="ts">
    import { AgentControl } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let { view, onCall }: { view: AgentOffersView; onCall: (key: string) => void } = $props();
</script>

<!-- A refusal stays readable when the frame that refused also takes the control away. -->
{#if view.shown || view.refusal}
    <AgentControl rows={view.rows} {onCall} refusal={view.refusal} />
{/if}
