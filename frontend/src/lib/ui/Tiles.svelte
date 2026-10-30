<script lang="ts">
    import { qualityPill, remoteMedia } from "#lib/ui/stage-media.ts";
    import { remoteTiles } from "#lib/ui/stage.svelte.ts";
</script>

{#each remoteTiles as tile (tile.id)}
    <div class="tile" class:speaking={tile.isSpeaking} id="container-{tile.id}">
        <video
            id="video-{tile.id}"
            autoplay
            class:hidden={!tile.showsVideo}
            use:remoteMedia={{ participantId: tile.id, delivery: tile.streamDelivery }}
        ></video>
        <div class="vid off" class:hidden={tile.showsVideo} id="placeholder-{tile.id}">
            <span class="av"
                ><svg
                    class="icon"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                    ><circle cx="9" cy="8" r="3.5" /><path d="M2.5 19.5c.7-3.4 3.3-5.2 6.5-5.2s5.8 1.8 6.5 5.2" /></svg
                ></span
            >
        </div>
        <!-- prettier-ignore -->
        <span class="name"><span class="muted" class:hidden={!tile.isMuted}><svg class="icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 9.5V6a3 3 0 0 0-5.6-1.5M9 9v2.5a3 3 0 0 0 4.6 2.5M18.5 11a6.5 6.5 0 0 1-1 3.4M5.5 11a6.5 6.5 0 0 0 10 5.5M12 17.5V21M4 4l16 16"/></svg></span><span class="wave" class:hidden={!tile.isSpeaking} aria-hidden="true"><i></i><i></i><i></i></span>{tile.name}</span>
        <div class="connection-quality" use:qualityPill={{ participantId: tile.id, delivery: tile.qualityDelivery }}>
            <div class="quality-bar"></div>
            <div class="quality-bar"></div>
            <div class="quality-bar"></div>
        </div>
    </div>
{/each}
