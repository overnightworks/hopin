<script lang="ts">
    import type { Table } from "./table.svelte.ts";

    let { src, table }: { src: string; table: Table } = $props();
</script>

<div class="skat-table">
    <iframe
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
