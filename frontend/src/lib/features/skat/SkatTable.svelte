<script lang="ts">
    import { greetingFor } from "./messages.ts";
    import type { Table } from "./table.svelte.ts";

    let { src, table }: { src: string; table: Table } = $props();

    let frame = $state<HTMLIFrameElement>();
    const frameOrigin = $derived(new URL(src).origin);

    $effect(() => {
        const answer = (event: MessageEvent) => {
            const greeting = greetingFor(
                { origin: event.origin, source: event.source, frameWindow: frame?.contentWindow, data: event.data },
                frameOrigin,
                table.current,
            );
            if (greeting !== null) (event.source as Window).postMessage(greeting.hello, greeting.targetOrigin);
        };
        window.addEventListener("message", answer);
        return () => {
            window.removeEventListener("message", answer);
        };
    });
</script>

<div class="skat-table">
    <iframe
        bind:this={frame}
        class="skat-frame"
        title="Skat table"
        {src}
        sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        allow=""
        referrerpolicy="no-referrer"
    ></iframe>
    {#if table.mayRemove}
        <button
            class="button skat-remove"
            type="button"
            onclick={() => {
                table.remove();
            }}>Remove table</button
        >
    {/if}
</div>
