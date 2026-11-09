// The seam where a feature's browser module reaches the call core. The page hands `FeatureCore` to each
// feature's `install` before signaling connects; besides `featureView`, which builds what it hands over, a
// feature imports only the generated wires and the core's types.

import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import type { TOAST_KIND, ToastAction, ToastKind } from "#lib/ui/toast.svelte.ts";

export interface OutgoingFrame {
    type: string;
    [field: string]: unknown;
}

export const AGENT_ACTIVITY = {
    THINKING: "thinking",
    WAITING: "waiting",
    SPEAKING: "speaking",
    PRESENT: "present",
} as const;
export type AgentActivity = (typeof AGENT_ACTIVITY)[keyof typeof AGENT_ACTIVITY];

/** What the stage shows of an agent in the call: a tile with its name, an AI badge and what it is doing; a dialed-in agent also names its sponsor and offers a remove action to whoever may use it. */
export interface AgentTile {
    name: string;
    activity: AgentActivity;
    sponsor?: string;
    onRemove?: () => void;
}

/** The key of the tile a feature shows without naming one: a feature with several agents gives each its own. */
export const DEFAULT_AGENT_KEY = "agent";

export interface FeatureCore {
    /** The handler declares the fields of the frames it registers for; the server it trusts sends them. */
    on(type: string, handler: (frame: never) => void): void;
    send(frame: OutgoingFrame): void;
    isConnected(): boolean;
    showToast(text: string, kind: ToastKind, action: ToastAction | null): void;
    toastKind: typeof TOAST_KIND;
    addMoreItems(...items: Element[]): void;
    /** Close the More menu, as after an action in one of its items that shows its effect elsewhere. */
    closeMore(): void;
    /** Show a line no person wrote, counted as unread like a person's. */
    receiveChatLine(view: FeatureView): void;
    /** Keep a view below every later line until another view, or null, takes its place. */
    pinLastChatLine(view: FeatureView | null): void;
    /** Show a view between the chat's header and its lines until another view, or null, takes its place. */
    showChatCard(view: FeatureView | null): void;
    /** Show an agent's tile on the stage under its key, beside the people's and taking none of the call's places, until null takes it away. */
    showAgentTile(tile: AgentTile | null, key?: string): void;
    /** Show a view on the stage; the tiles move into a strip until another view, or null, takes its place; the call's end clears it. */
    showStage(view: FeatureView | null): void;
    onCallEnded(hook: () => void): void;
}

export interface FeatureModule {
    install(core: FeatureCore): Promise<void>;
}
