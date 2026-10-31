<script lang="ts">
    import type { QuestionCardProps } from "./quiz.svelte.ts";

    let { tag, question, canReveal, revealDisabled, locked, status, sent, reveal, answer }: QuestionCardProps =
        $props();
    let input: HTMLInputElement;

    function submit(event: SubmitEvent) {
        event.preventDefault();
        answer(input.value);
    }
</script>

<!-- prettier-ignore -->
<section class="qcard" aria-label="Quiz question"><div class="qcard-head"><span class="qcard-tag"><svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/></svg>{tag}</span>{#if canReveal}<button type="button" class="qcard-reveal" disabled={revealDisabled} onclick={reveal}>Reveal</button>{/if}</div><p class="qcard-q">{question}</p><form class="qcard-form" onsubmit={submit}><div class="qcard-row"><input class="qcard-input" type="text" autocomplete="off" placeholder="Your answer" aria-label="Your answer" disabled={locked} bind:this={input}><button type="submit" class="qcard-send" disabled={locked}>Send</button></div><p class={["qcard-status", sent && "sent"]} aria-live="polite">{status}</p></form></section>
