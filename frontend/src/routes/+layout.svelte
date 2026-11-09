<script lang="ts">
    import "overnightworks-design/design.css";
    import type { Snippet } from "svelte";

    let { children }: { children: Snippet } = $props();
</script>

{@render children()}
