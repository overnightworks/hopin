<script lang="ts">
    import { AgentControl, type KitWords } from "overnightworks-design";
    import type { AgentOffersView } from "./offers.svelte.ts";

    let { view, onCall, words }: { view: AgentOffersView; onCall: (key: string) => void; words: KitWords } = $props();

    // A seat that may not add agents still sees the agents in the call and the outcome they state.
    const rows = $derived(view.shown ? view.rows : view.rows.filter((row) => row.state === "here"));
</script>

<!-- A refusal stays readable when the frame that refused also takes the control away. -->
{#if view.shown || view.refusal || rows.length > 0}
    <AgentControl {rows} full={view.full} {onCall} refusal={view.refusal} {words} />
    <!-- The control states a full call only in place of its rows, so agents in the call must not hide it. -->
    {#if view.shown && view.full && rows.length > 0}
        <p class="full">{words["agent.placeFull"]}</p>
    {/if}
{/if}

<style>
    .full {
        margin: 0;
        padding: 0 14px 12px;
        color: var(--text-muted);
        font-size: 13.5px;
        line-height: 1.4;
    }
</style>
