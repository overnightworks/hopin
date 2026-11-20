<script lang="ts">
    import { AgentControl, type KitWords } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let { view, onCall, words }: { view: AgentOffersView; onCall: (key: string) => void; words: KitWords } = $props();

    // A seat that may not add agents still sees the agents in the call and the outcome they state.
    const rows = $derived(view.shown ? view.rows : view.rows.filter((row) => row.state === "here"));
</script>

<!-- A refusal stays readable when the frame that refused also takes the control away. -->
{#if view.shown || view.refusal || rows.length > 0}
    <AgentControl {rows} full={view.shown && view.full} {onCall} refusal={view.refusal} {words} />
{/if}
