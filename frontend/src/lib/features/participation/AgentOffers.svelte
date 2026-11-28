<script lang="ts">
    import { AgentControl, type KitWords } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let {
        view,
        onCall,
        onSendAway,
        onOutcomeEnd,
        words,
    }: {
        view: AgentOffersView;
        onCall: (key: string) => void;
        onSendAway: (key: string) => void;
        onOutcomeEnd: (key: string) => void;
        words: KitWords;
    } = $props();

    // A here row is only for an agent the seat may send away (the page builds none else); a seat that may not add agents
    // sees those rows and no others.
    const rows = $derived(view.shown ? view.rows : view.rows.filter((row) => row.state === "here"));
</script>

<!-- A refusal stays readable when the frame that refused also takes the control away. -->
{#if view.shown || view.refusal || rows.length > 0}
    <AgentControl
        {rows}
        full={view.shown && view.full}
        {onCall}
        {onSendAway}
        {onOutcomeEnd}
        refusal={view.refusal}
        {words}
    />
{/if}
