<script lang="ts">
    import { TOAST_KIND, toastElement, toastState } from "#lib/ui/toast.svelte.ts";
</script>

<output id="toasts" class="toasts">
    {#if toastState.current}
        {@const toast = toastState.current}
        {#key toast.id}
            <div class="toast" bind:this={toastElement.current} class:leaving={toast.leaving}>
                <svg
                    class="icon"
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    stroke-width="2.2"
                    stroke-linecap="round"
                    stroke-linejoin="round"
                    aria-hidden="true"
                >
                    {#if toast.kind === TOAST_KIND.DONE}
                        <path d="M5 12.5l4.5 4.5L19 7.5" />
                    {:else if toast.kind === TOAST_KIND.FAILED}
                        <path d="M6 6l12 12M18 6L6 18" />
                    {:else if toast.kind === TOAST_KIND.LOCKED}
                        <rect x="4.5" y="10.5" width="15" height="10" rx="2.6" />
                        <path d="M8 10.5V7.8a4 4 0 0 1 8 0v2.7" />
                    {:else if toast.kind === TOAST_KIND.WARNING}
                        <path d="M12 3.8l9 15.7H3z" />
                        <path d="M12 10v4.4M12 17.2v.1" />
                    {:else}
                        <path d="M6.5 3.5h11M6.5 20.5h11M8 3.5v3a4 4 0 0 0 8 0v-3M8 20.5v-3a4 4 0 0 1 8 0v3" />
                    {/if}
                </svg>
                <span>{toast.text}</span>
                {#if toast.action}
                    <button type="button" class="toast-act" onclick={toast.action.onAct}>{toast.action.label}</button>
                {/if}
            </div>
        {/key}
    {/if}
</output>
