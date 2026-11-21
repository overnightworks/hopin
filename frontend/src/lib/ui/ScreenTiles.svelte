<script lang="ts">
    import { screenMedia } from "#lib/ui/stage-media.ts";
    import { screenShares } from "#lib/ui/stage.svelte.ts";
</script>

{#if screenShares.length > 0}
    <div class="screens">
        {#each screenShares as share (share.participantId)}
            <div class="tile screen" id={"screen-" + share.participantId}>
                <video
                    id={"screen-video-" + share.participantId}
                    autoplay
                    playsinline
                    use:screenMedia={{ participantId: share.participantId, delivery: share.streamDelivery }}
                ></video>
                <span class="name"><span class="name-text">{share.name}</span></span>
            </div>
        {/each}
    </div>
{/if}
