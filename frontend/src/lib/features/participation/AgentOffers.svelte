<script lang="ts">
    import { AgentControl, type KitWords } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let { view, onCall, words }: { view: AgentOffersView; onCall: (key: string) => void; words: KitWords } = $props();
</script>

<!-- A refusal stays readable when the frame that refused also takes the control away. -->
{#if view.shown || view.refusal}
    <AgentControl rows={view.rows} full={view.full} {onCall} refusal={view.refusal} {words} />
{/if}
