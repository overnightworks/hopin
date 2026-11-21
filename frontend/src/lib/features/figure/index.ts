import {
    AGENT_FIGURE,
    type AgentFigure,
    type AgentFigureDrawer,
    type AgentFigureHandle,
    type AgentGestureClips,
    type AgentSentence,
    type FeatureCore,
} from "#lib/call/feature-port.ts";
import { FIGURE_END } from "#lib/features/participation/wire.ts";
import type {
    FigureFraming,
    FigureGesture,
    FigureHandle,
    FigureMood,
    FigureSignal,
    MountOptions,
} from "overnightworks-figure/draw";

type FigureDrawing = Promise<typeof import("overnightworks-figure/draw")>;

// How long the figure waits for its clips: a clip the server cannot fetch has the server's own deadline of seconds, and the figure is not held for it.
const CLIP_WAIT_MS = 10_000;

async function fetchBytes(from: string, signal: AbortSignal) {
    const response = await fetch(from, { signal });
    if (!response.ok) throw new Error(`${from} answered ${String(response.status)}`);
    return new Uint8Array(await response.arrayBuffer());
}

// Why a figure ended, by the signal that ended it; a figure that could not be fetched or loaded ends as `unloaded`.
const ENDING: Record<Exclude<FigureSignal, "drawn">, FIGURE_END> = {
    unsupported: FIGURE_END.UNSUPPORTED,
    refused: FIGURE_END.REFUSED,
    lost: FIGURE_END.LOST,
    slow: FIGURE_END.SLOW,
};

// The figure shows no mood for a name that is no mood of its own, so the page checks none.
function speakTo(handle: FigureHandle, sentence: AgentSentence | null) {
    handle.speak(sentence === null ? null : { voice: sentence.voice, mood: sentence.mood as FigureMood | undefined });
}

// One figure on one tile: the bytes come over a stoppable fetch, and only then does a fresh canvas join the host.
function drawFigure(
    host: HTMLElement,
    path: string,
    show: (figure: AgentFigure, reason?: string) => void,
    drawing: FigureDrawing,
    clipPaths: AgentGestureClips,
    firstFraming: string | undefined,
    roomPath: string | undefined,
): AgentFigureHandle {
    const fetching = new AbortController();
    let canvas: HTMLCanvasElement | undefined;
    let handle: FigureHandle | undefined;
    let speaker: Element | null = null;
    let sentence: AgentSentence | null = null;
    let thinking = false;
    // A framing the page names that figure does not know is dropped by the figure itself.
    let framing = firstFraming as FigureFraming | undefined;
    // The persona's room once its bytes have arrived; the figure is never held for them.
    let roomBytes: Uint8Array | undefined;
    let over = false;
    // A function, so the checks after an await do not read as settled.
    const hasEnded = () => over;

    function end(reason: FIGURE_END | null) {
        if (over) return;
        over = true;
        fetching.abort();
        handle?.dispose();
        canvas?.remove();
        if (reason !== null) show(AGENT_FIGURE.ENDED, reason);
    }

    function heard(signal: FigureSignal) {
        if (over) return;
        if (signal === "drawn") show(AGENT_FIGURE.DRAWN);
        else end(ENDING[signal]);
    }

    // A clip that cannot be fetched is left out: the figure plays its own gesture.
    async function fetchClips(): Promise<NonNullable<MountOptions["gestures"]>> {
        const waiting = new AbortController();
        const giveUp = () => {
            waiting.abort();
        };
        const timer = setTimeout(giveUp, CLIP_WAIT_MS);
        fetching.signal.addEventListener("abort", giveUp);
        const fetched = await Promise.all(
            Object.entries(clipPaths).map(([name, clipPath]) =>
                fetchBytes(clipPath, waiting.signal).then(
                    (bytes) => [name, bytes] as const,
                    () => null,
                ),
            ),
        );
        clearTimeout(timer);
        fetching.signal.removeEventListener("abort", giveUp);
        return Object.fromEntries(fetched.filter((clip) => clip !== null));
    }

    // A room that cannot be fetched is left out: figure shows none and nothing is called.
    function fetchRoom(path: string) {
        fetchBytes(path, fetching.signal).then(
            (bytes) => {
                roomBytes = bytes;
                if (!over) handle?.room(bytes);
            },
            () => undefined,
        );
    }

    async function run() {
        if (roomPath !== undefined) fetchRoom(roomPath);
        const [bytes, gestures, { mount }] = await Promise.all([
            fetchBytes(path, fetching.signal),
            fetchClips(),
            drawing,
        ]);
        if (hasEnded()) return;
        canvas = document.createElement("canvas");
        canvas.className = "figure-canvas";
        // The page's policy forbids a style attribute in markup; script sets the size the package follows.
        canvas.style.width = "100%";
        canvas.style.height = "100%";
        host.prepend(canvas);
        const mountedIn = framing;
        const mountedRoom = roomBytes;
        const mounted = await mount(canvas, bytes, {
            onSignal: heard,
            gestures,
            framing: mountedIn,
            room: mountedRoom,
        });
        if (hasEnded()) {
            mounted.dispose();
            return;
        }
        handle = mounted;
        // A framing taken while the figure mounted is shown now.
        if (framing !== undefined && framing !== mountedIn) handle.frame(framing);
        // A room that arrived while the figure mounted is shown now.
        if (roomBytes !== undefined && roomBytes !== mountedRoom) handle.room(roomBytes);
        if (speaker !== null) handle.listen(speaker);
        if (sentence !== null) speakTo(handle, sentence);
        if (thinking) handle.think(true);
    }

    run().catch(() => {
        end(FIGURE_END.UNLOADED);
    });
    return {
        stop: () => {
            end(null);
        },
        // The handle drops a name that is no gesture of figure's, so the page checks none.
        gesture: (name) => {
            handle?.gesture(name as FigureGesture);
        },
        frame: (name) => {
            framing = name as FigureFraming;
            handle?.frame(framing);
        },
        listen: (next) => {
            speaker = next;
            handle?.listen(next);
        },
        speak: (next) => {
            sentence = next;
            if (handle !== undefined) speakTo(handle, next);
        },
        think: (working) => {
            thinking = working;
            handle?.think(working);
        },
    };
}

export function install(core: FeatureCore): Promise<void> {
    const drawing: FigureDrawing = import("overnightworks-figure/draw");
    // A failed import is reported by each figure that awaits it; one that fails before the first drawer awaits it is no unhandled rejection.
    drawing.catch(() => undefined);
    const draws: AgentFigureDrawer = (host, path, show, gestures, framing, room) =>
        drawFigure(host, path, show, drawing, gestures ?? {}, framing, room);
    core.drawAgentFigures(draws);
    return Promise.resolve();
}
