// The seam where a feature's browser module reaches the call core. The page hands `FeatureCore` to each
// feature's `install` before signaling connects; besides `featureView`, which builds what it hands over, a
// feature imports only the generated wires (the core's `lib/wire.ts` and its own `wire.ts`) and the core's types.
// The slots are the call core's few: a frame handler, sending a frame and asking whether the socket is connected,
// a toast, an item in More, a chat line no person wrote or a line pinned last, a card between the chat's header
// and its lines, the agents that read the chat's lines, a view on the stage, an agent's tile and the drawer of its
// figure, the person's microphone, a hook for the call's end and the start link the page was opened at. The three chat slots take a feature view, a component bound to its props that a feature
// builds with core chat's `featureView`; the core renders it and never names what it shows or any agent kind.

import type { TurnOutcome } from "overnightworks-design";
import type { AgentLink } from "#lib/call/lines.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import type { StartLink } from "#lib/ui/navigation.ts";
import type { TOAST_KIND, ToastAction, ToastKind } from "#lib/ui/toast.svelte.ts";
import type { ServerFrameByType } from "#lib/wire.ts";

export interface OutgoingFrame {
    type: string;
    [field: string]: unknown;
}

export interface SendOptions {
    transient?: boolean;
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

/** A sentence the agent speaks: the audio node its voice plays through, which the mouth follows, and the name of the mood its face shows; a field it lacks leaves the mouth still or the face neutral. */
export interface AgentSentence {
    voice?: AudioNode;
    mood?: string;
}

/** What a drawn figure offers the page: `stop` takes the figure away and is the last the drawer reports; `gesture` plays the gesture of that name once, and is dropped while the figure is loading; `frame` takes the framing of that name, which a figure still loading shows once it is drawn; `listen` turns the figure toward the tile of whoever speaks, null toward nobody; `speak` makes the figure speak the sentence, null makes it stop; `think` shows whether its agent thinks; one `listen`, `speak` or `think` given while the figure loads holds once it is drawn. */
export interface AgentFigureHandle {
    stop: () => void;
    gesture: (name: string) => void;
    frame: (name: string) => void;
    listen: (speaker: Element | null) => void;
    speak: (sentence: AgentSentence | null) => void;
    think: (working: boolean) => void;
}

/** The same-origin paths of a persona's clips, by the name of the gesture each replaces. */
export type AgentGestureClips = Readonly<Record<string, string>>;

/** Draws the figure at the same-origin `path` into `host`, with the clips at `gestures` where it has any, behind it the persona's room read from the same-origin path `room` when it has one, seen in the framing named `framing` from its first frame (the figure's own default when none), and reports whether it is `DRAWN` or `ENDED`, an end with the `reason` it ended for; the tile shows its symbol until `DRAWN`. */
export type AgentFigureDrawer = (
    host: HTMLElement,
    path: string,
    show: (figure: AgentFigure, reason?: string) => void,
    gestures?: AgentGestureClips,
    framing?: string,
    room?: string,
) => AgentFigureHandle;

/** The switch on an agent's tile that lets the agent hear the person: the tile shows `on` and hands each press, as the value it asks for, to `onChange`; it holds no state of its own. */
export interface AgentHearing {
    on: boolean;
    onChange: (on: boolean) => void;
}

/** The switch on an agent's tile that lets the person hear the agent in their own browser: the tile shows `on` and hands each press, as the value it asks for, to `onChange`; it holds no state of its own. */
export interface AgentListening {
    on: boolean;
    onChange: (on: boolean) => void;
}

/** The switch on an agent's tile that lets the agent see the person: the tile shows `on` and hands each press, as the value it asks for, to `onChange`; it holds no state of its own. */
export interface AgentSeeing {
    on: boolean;
    onChange: (on: boolean) => void;
}

/** What the stage shows of an agent in the call: a tile with its name, an AI badge and what it is doing; a dialed-in agent also names its sponsor, one with a stated `turnState` shows it beside the name, and one with a figure names the same-origin path that serves it the paths of the clips it brings, the path of the room it is seen in and the framing its agent has taken; a tile with `hearing`, `listening` or `seeing` carries the person's switch for the agent, and a tile with `looking` shows the mark of a look. */
export interface AgentTile {
    name: string;
    activity: AgentActivity;
    sponsor?: string;
    figure?: string;
    gestures?: AgentGestureClips;
    room?: string;
    framing?: string;
    hearing?: AgentHearing;
    listening?: AgentListening;
    seeing?: AgentSeeing;
    looking?: boolean;
    turnState?: TurnOutcome;
}

/** The key of the tile a feature shows without naming one: a feature with several agents gives each its own. */
export const DEFAULT_AGENT_KEY = "agent";

/** What a still picture of the person's camera may be: the longer side in pixels, the encoded picture's bytes and the time the camera may take for its frame. */
export interface PictureBounds {
    maxPixels: number;
    maxBytes: number;
    maxMilliseconds: number;
}

/** `Frames` is the feature's own `<Feature>FrameByType`; a handler takes the frame of the signal it registers for, from the feature's frames or the core's. */
export interface FeatureCore<Frames extends object = object> {
    on<Type extends (keyof Frames | keyof ServerFrameByType) & string>(
        type: Type,
        handler: (frame: (Frames & ServerFrameByType)[Type]) => void,
    ): void;
    /** A `transient` frame is sent once and nothing keeps it: a reconnect does not replay it and one sent while the session is not live is dropped, so a payload that must not outlast its use is not retained. */
    send(frame: OutgoingFrame, options?: SendOptions): void;
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
    /** Name the agents the page shows as joined and say how a person's line reaches them: the chat sends each line through `send` while an agent is named, and says above its composer who reads along. Called again with none named when the last leaves. */
    setLineReaders(link: AgentLink): void;
    /** Show an agent's tile on the stage under its key, beside the people's and taking none of the call's places, until null takes it away. */
    showAgentTile(tile: AgentTile | null, key?: string): void;
    /** Hand the drawer that draws the figure of each agent tile that names one. The core keeps it and an action on the tile's video layer, which writes the figure's state as `data-figure` and stops the figure when the tile goes or names another path. */
    drawAgentFigures(drawer: AgentFigureDrawer): void;
    /** Register a handler told, with the key of the agent tile and the reason, each time that tile's figure ends; the tile keeps the reason as `data-figure-end`. */
    onAgentFigureEnded(hook: (key: string, reason: string) => void): void;
    /** Play the gesture on the figure of the agent tile shown under `key`; dropped when that tile has no drawn figure. */
    gestureAgentFigure(key: string, gesture: string): void;
    /** Show the figure of the agent tile shown under `key` in the framing named `framing`; dropped when that tile has no drawn figure. */
    frameAgentFigure(key: string, framing: string): void;
    /** Make the figure of the agent tile shown under `key` speak the sentence, null stop; dropped when that tile has no figure. */
    speakAgentFigure(key: string, sentence: AgentSentence | null): void;
    /** Show whether the agent of the tile under `key` thinks, until the next call or the tile goes; a figure drawn on that tile meanwhile starts so. */
    thinkAgentFigure(key: string, thinking: boolean): void;
    /** Show a view on the stage; the tiles move into a strip at the top, right-aligned, and picture-in-picture is off, until another view, or null, takes its place; the call's end clears it. */
    showStage(view: FeatureView | null): void;
    /** Register a handler for the person's microphone; it is told the current state at once and then on every change: a stream that holds only the local microphone track while the person is in a call and the microphone is on, and null otherwise (the pre-join included). The stream is never a screen share's audio, a peer's or an agent's. */
    onMicrophone(handler: (stream: MediaStream | null) => void): void;
    /** One still picture of the person's own camera as JPEG base64 within the bounds, or null when no camera is on: the camera is off, a screen share replaces it, or the person is not in a call. Never a screen share's, a peer's or an agent's picture. */
    takePicture(bounds: PictureBounds): Promise<string | null>;
    onCallEnded(hook: () => void): void;
    /** The start link the page was opened at, null at any other address; it does not change, so a feature that acts on it does so once. */
    startLink: StartLink | null;
}

export interface FeatureModule {
    install(core: FeatureCore<never>): Promise<void>;
}
