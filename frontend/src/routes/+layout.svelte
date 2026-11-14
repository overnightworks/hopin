<script lang="ts">
    import "overnightworks-design/design.css";
    import "#lib/assets/hopin.css";
    import icon from "#lib/assets/hopin.svg?no-inline";
    import type { Snippet } from "svelte";

    let { children }: { children: Snippet } = $props();
</script>

<svelte:head>
    <link rel="icon" type="image/svg+xml" href={icon} />
</svelte:head>

{@render children()}
