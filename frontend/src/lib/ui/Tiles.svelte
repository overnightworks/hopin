<script lang="ts">
    import { HearingSwitch, loadKitWords, type KitWords } from "overnightworks-design";
    import { onMount } from "svelte";
    import { qualityPill, remoteMedia } from "#lib/ui/stage-media.ts";
    import { words } from "#lib/words/de.ts";
    import { sourceLanguage } from "#lib/words/index.ts";
    import { AGENT_ACTIVITY } from "#lib/call/feature-port.ts";
    import { agentFigure, agentTiles, remoteTiles } from "#lib/ui/stage.svelte.ts";

    const ACTIVITY_LABEL = {
        [AGENT_ACTIVITY.THINKING]: words["tile.agentThinkingLabel"],
        [AGENT_ACTIVITY.WAITING]: words["tile.agentWaitingLabel"],
        [AGENT_ACTIVITY.SPEAKING]: words["tile.agentSpeakingLabel"],
        [AGENT_ACTIVITY.PRESENT]: words["tile.agentPresentLabel"],
    };

    let kitWords = $state<KitWords>();
    onMount(async () => {
        kitWords = await loadKitWords(sourceLanguage);
    });
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
        <span class="name"><span class="muted" class:hidden={!tile.isMuted}><svg class="icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 9.5V6a3 3 0 0 0-5.6-1.5M9 9v2.5a3 3 0 0 0 4.6 2.5M18.5 11a6.5 6.5 0 0 1-1 3.4M5.5 11a6.5 6.5 0 0 0 10 5.5M12 17.5V21M4 4l16 16"/></svg></span><span class="wave" class:hidden={!tile.isSpeaking} aria-hidden="true"><i></i><i></i><i></i></span><span class="name-text">{tile.name}</span></span>
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
    {@const tileLabel = ACTIVITY_LABEL[agent.activity]({ name: agent.name })}
    <div
        class={tileClass}
        class:speaking={agent.activity === AGENT_ACTIVITY.SPEAKING}
        id={tileId}
        role="status"
        aria-label={tileLabel}
    >
        <div class="vid off" use:agentFigure={agent.figure}>
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
        {#if agent.hearing !== undefined && kitWords !== undefined}
            <div class="hear-set">
                <HearingSwitch kind="hear" on={agent.hearing.on} onChange={agent.hearing.onChange} words={kitWords} />
            </div>
        {/if}
        {#if agent.onRemove !== undefined}
            <button class="agent-remove" type="button" aria-label={words["tile.removeButton"]} onclick={agent.onRemove}>
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
            ><span class="name-text">{agent.name}</span><span class="ai-badge">{words["tile.aiBadge"]}</span
            >{#if agent.sponsor !== undefined}<span class="sponsor">{agent.sponsor}</span>{/if}</span
        >
    </div>
{/each}

<style>
    /* The kit's chip takes the stage accent: the app's own accent is dark in the light theme and would sink into the glass over a dark video. The chip's narrowest look is its icon and switch with the label shortened away, 81px: 15px of padding, the 16px icon, two 8px gaps and the 34px switch. */
    .hear-set {
        --accent: var(--accent-stage);
        --narrowest: 81px;
        position: absolute;
        top: 8px;
        left: 8px;
        z-index: 7;
        min-width: var(--narrowest);
        max-width: calc(100% - 64px);
    }
    /* Under 131px the narrowest chip no longer fits beside the remove button (8px, the chip, the button's 32px and its 10px), so it moves into the tile's lower right corner, clear of the button. */
    @container (max-width: 130.99px) {
        .hear-set {
            top: auto;
            left: auto;
            right: 2px;
            bottom: 2px;
            width: var(--narrowest);
            max-width: none;
        }
    }
</style>
