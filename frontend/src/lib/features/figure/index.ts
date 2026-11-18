import {
    AGENT_FIGURE,
    type AgentFigure,
    type AgentFigureDrawer,
    type AgentFigureHandle,
    type AgentGestureClips,
    type AgentSentence,
    type FeatureCore,
} from "#lib/call/feature-port.ts";
import type { FigureGesture, FigureHandle, FigureMood, FigureSignal, MountOptions } from "overnightworks-figure/draw";

type FigureDrawing = Promise<typeof import("overnightworks-figure/draw")>;

// How long the figure waits for its clips: a clip the server cannot fetch has the server's own deadline of seconds, and the figure is not held for it.
const CLIP_WAIT_MS = 10_000;

async function fetchBytes(from: string, signal: AbortSignal) {
    const response = await fetch(from, { signal });
    if (!response.ok) throw new Error(`${from} answered ${String(response.status)}`);
    return new Uint8Array(await response.arrayBuffer());
}

// The figure shows no mood for a name that is no mood of its own, so the page checks none.
function speakTo(handle: FigureHandle, sentence: AgentSentence | null) {
    handle.speak(sentence === null ? null : { voice: sentence.voice, mood: sentence.mood as FigureMood | undefined });
}

// One figure on one tile: the bytes come over a stoppable fetch, and only then does a fresh canvas join the host.
function drawFigure(
    host: HTMLElement,
    path: string,
    show: (figure: AgentFigure) => void,
    drawing: FigureDrawing,
    clipPaths: AgentGestureClips,
): AgentFigureHandle {
    const fetching = new AbortController();
    let canvas: HTMLCanvasElement | undefined;
    let handle: FigureHandle | undefined;
    let speaker: Element | null = null;
    let sentence: AgentSentence | null = null;
    let over = false;
    // A function, so the checks after an await do not read as settled.
    const hasEnded = () => over;

    function end(report: AgentFigure | null) {
        if (over) return;
        over = true;
        fetching.abort();
        handle?.dispose();
        canvas?.remove();
        if (report !== null) show(report);
    }

    function heard(signal: FigureSignal) {
        if (over) return;
        if (signal === "drawn") show(AGENT_FIGURE.DRAWN);
        else end(AGENT_FIGURE.ENDED);
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

    async function run() {
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
        const mounted = await mount(canvas, bytes, { onSignal: heard, gestures });
        if (hasEnded()) {
            mounted.dispose();
            return;
        }
        handle = mounted;
        if (speaker !== null) handle.listen(speaker);
        if (sentence !== null) speakTo(handle, sentence);
    }

    run().catch(() => {
        end(AGENT_FIGURE.ENDED);
    });
    return {
        stop: () => {
            end(null);
        },
        // The handle drops a name that is no gesture of figure's, so the page checks none.
        gesture: (name) => {
            handle?.gesture(name as FigureGesture);
        },
        listen: (next) => {
            speaker = next;
            handle?.listen(next);
        },
        speak: (next) => {
            sentence = next;
            if (handle !== undefined) speakTo(handle, next);
        },
    };
}

export function install(core: FeatureCore): Promise<void> {
    const drawing: FigureDrawing = import("overnightworks-figure/draw");
    // A failed import is reported by each figure that awaits it; one that fails before the first drawer awaits it is no unhandled rejection.
    drawing.catch(() => undefined);
    const draws: AgentFigureDrawer = (host, path, show, gestures = {}) =>
        drawFigure(host, path, show, drawing, gestures);
    core.drawAgentFigures(draws);
    return Promise.resolve();
}
