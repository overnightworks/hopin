<script lang="ts">
    import { qualityPill, remoteMedia } from "#lib/ui/stage-media.ts";
    import { AGENT_ACTIVITY } from "#lib/call/feature-port.ts";
    import { agentTiles, remoteTiles } from "#lib/ui/stage.svelte.ts";

    const ACTIVITY_LABEL = {
        [AGENT_ACTIVITY.THINKING]: "thinking",
        [AGENT_ACTIVITY.WAITING]: "waiting for answers",
        [AGENT_ACTIVITY.SPEAKING]: "speaking",
        [AGENT_ACTIVITY.PRESENT]: "in the call",
    };
</script>

{#each remoteTiles as tile (tile.id)}
    <div class="tile" class:speaking={tile.isSpeaking} id={"container-" + tile.id}>
        <video
            id={"video-" + tile.id}
            autoplay
            class:hidden={!tile.showsVideo}
            use:remoteMedia={{ participantId: tile.id, delivery: tile.streamDelivery }}
        ></video>
        <div class="vid off" class:hidden={tile.showsVideo} id={"placeholder-" + tile.id}>
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
{#each [...agentTiles] as [key, agent] (key)}
    {@const tileClass = "tile agent activity-" + agent.activity}
    {@const tileId = "container-" + key}
    {@const tileLabel = agent.name + ", AI, " + ACTIVITY_LABEL[agent.activity]}
    <div
        class={tileClass}
        class:speaking={agent.activity === AGENT_ACTIVITY.SPEAKING}
        id={tileId}
        role="status"
        aria-label={tileLabel}
    >
        <div class="vid off">
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
                    ><path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z" /><path
                        d="M18.5 3v4M16.5 5h4"
                    /></svg
                ></span
            >
            <span class="agent-activity" aria-hidden="true"><i></i><i></i><i></i></span>
        </div>
        {#if agent.onRemove !== undefined}
            <button class="agent-remove" type="button" aria-label="Remove" onclick={agent.onRemove}>
                <svg
                    class="icon"
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.4"
                    stroke-linecap="round"
                    aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg
                >
            </button>
        {/if}
        <span class="name"
            ><span class="wave" class:hidden={agent.activity !== AGENT_ACTIVITY.SPEAKING} aria-hidden="true"
                ><i></i><i></i><i></i></span
            >{agent.name}<span class="ai-badge">AI</span>{#if agent.sponsor !== undefined}<span class="sponsor"
                    >{agent.sponsor}</span
                >{/if}</span
        >
    </div>
{/each}
