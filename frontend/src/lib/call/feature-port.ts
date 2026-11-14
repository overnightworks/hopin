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

export const AGENT_FIGURE = {
    LOADING: "loading",
    DRAWN: "drawn",
    ENDED: "ended",
} as const;
export type AgentFigure = (typeof AGENT_FIGURE)[keyof typeof AGENT_FIGURE];

/** Draws the figure at the same-origin `path` into `host` and reports whether it is `DRAWN` or `ENDED`; the tile shows its symbol until `DRAWN`. The returned stop takes the figure away and is the last the drawer reports. */
export type AgentFigureDrawer = (host: HTMLElement, path: string, show: (figure: AgentFigure) => void) => () => void;

/** The switch on an agent's tile that lets the agent hear the person: the tile shows `on` and hands each press, as the value it asks for, to `onChange`; it holds no state of its own. */
export interface AgentHearing {
    on: boolean;
    onChange: (on: boolean) => void;
}

/** The switch on an agent's tile that lets the agent see the person: the tile shows `on` and hands each press, as the value it asks for, to `onChange`; it holds no state of its own. */
export interface AgentSeeing {
    on: boolean;
    onChange: (on: boolean) => void;
}

/** What the stage shows of an agent in the call: a tile with its name, an AI badge and what it is doing; a dialed-in agent also names its sponsor and offers a remove action to whoever may use it, and one with a figure names the same-origin path that serves it; a tile with `hearing` or `seeing` carries the person's switch for the agent, and a tile with `looking` shows the mark of a look. */
export interface AgentTile {
    name: string;
    activity: AgentActivity;
    sponsor?: string;
    figure?: string;
    hearing?: AgentHearing;
    seeing?: AgentSeeing;
    looking?: boolean;
    onRemove?: () => void;
}

/** The key of the tile a feature shows without naming one: a feature with several agents gives each its own. */
export const DEFAULT_AGENT_KEY = "agent";

/** What a still picture of the person's camera may be: the longer side in pixels and the encoded picture's bytes. */
export interface PictureBounds {
    maxPixels: number;
    maxBytes: number;
}

export interface FeatureCore {
    /** The handler declares the fields of the frames it registers for; the server it trusts sends them. */
    on(type: string, handler: (frame: never) => void): void;
    send(frame: OutgoingFrame): void;
    /** Sends a frame once and keeps nothing of it: a reconnect does not replay it and one sent while the session is not live is dropped, so a payload that must not outlast its use is not retained. */
    sendTransient(frame: OutgoingFrame): void;
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
    /** Hand the drawer that draws the figure of each agent tile that names one. */
    drawAgentFigures(drawer: AgentFigureDrawer): void;
    /** Show a view on the stage; the tiles move into a strip until another view, or null, takes its place; the call's end clears it. */
    showStage(view: FeatureView | null): void;
    /** Register a handler for the person's microphone; it is told the current state at once and then on every change: a stream that holds only the local microphone track while the person is in a call and the microphone is on, and null otherwise (the pre-join included). The stream is never a screen share's audio, a peer's or an agent's. */
    onMicrophone(handler: (stream: MediaStream | null) => void): void;
    /** One still picture of the person's own camera as JPEG base64 within the bounds, or null when no camera is on: the camera is off, a screen share replaces it, or the person is not in a call. Never a screen share's, a peer's or an agent's picture. */
    takePicture(bounds: PictureBounds): Promise<string | null>;
    onCallEnded(hook: () => void): void;
}

export interface FeatureModule {
    install(core: FeatureCore): Promise<void>;
}
