<script lang="ts">
    let { text }: { text: string } = $props();
</script>

<!-- prettier-ignore -->
<div class="chat-msg other game-master"><div class="chat-sender game-master-sender"><svg class="icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10.5 4l1.9 5.6 5.6 1.9-5.6 1.9-1.9 5.6-1.9-5.6L3 11.5l5.6-1.9z"/><path d="M18.5 3v4M16.5 5h4"/></svg>Game master<span class="ai-badge">AI</span></div>{text}</div>
