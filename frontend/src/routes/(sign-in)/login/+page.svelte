<script lang="ts">
    import SignIn from "#lib/ui/SignIn.svelte";
    import type { PageProps } from "./$types";

    const { data }: PageProps = $props();
</script>

<SignIn page="signed-out" words={data.kitWords} />
