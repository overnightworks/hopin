<script lang="ts">
    import { ownPictureMedia } from "#lib/ui/stage-media.ts";
    import { ownPicture } from "#lib/ui/stage.svelte.ts";
</script>

<video use:ownPictureMedia={ownPicture.delivery}></video>
