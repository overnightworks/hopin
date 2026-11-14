<script lang="ts">
    import { words } from "#lib/words/de.ts";

    interface Props {
        on: boolean;
        onChange: (on: boolean) => void;
    }

    let { on, onChange }: Props = $props();
</script>

<!-- The kit's HearingSwitch has no seeing kind; this chip is drawn like it until the kit has one. -->
<button
    class="see-switch"
    type="button"
    role="switch"
    aria-checked={on}
    aria-label={words["tile.seeSwitchLabel"]}
    onclick={() => {
        onChange(!on);
    }}
>
    <span class="icon">
        <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z" />
            <circle cx="12" cy="12" r="2.8" />
            {#if !on}<path d="M4 4l16 16" />{/if}
        </svg>
    </span>
    <span class="label">{words["tile.seeSwitchShortLabel"]}</span>
    <span class="track" aria-hidden="true"></span>
</button>
