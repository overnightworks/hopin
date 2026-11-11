import { AGENT_FIGURE, type AgentFigure, type AgentFigureDrawer, type FeatureCore } from "#lib/call/feature-port.ts";
import type { FigureHandle, FigureSignal } from "overnightworks-figure/draw";

type FigureDrawing = Promise<typeof import("overnightworks-figure/draw")>;

// One figure on one tile: the bytes come over a stoppable fetch, and only then does a fresh canvas join the host.
function drawFigure(host: HTMLElement, path: string, show: (figure: AgentFigure) => void, drawing: FigureDrawing) {
    const fetching = new AbortController();
    let canvas: HTMLCanvasElement | undefined;
    let handle: FigureHandle | undefined;
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

    async function fetchBytes() {
        const response = await fetch(path, { signal: fetching.signal });
        if (!response.ok) throw new Error(`${path} answered ${String(response.status)}`);
        return new Uint8Array(await response.arrayBuffer());
    }

    async function run() {
        const [bytes, { mount }] = await Promise.all([fetchBytes(), drawing]);
        if (hasEnded()) return;
        canvas = document.createElement("canvas");
        canvas.className = "figure-canvas";
        // The page's policy forbids a style attribute in markup; script sets the size the package follows.
        canvas.style.width = "100%";
        canvas.style.height = "100%";
        host.prepend(canvas);
        const mounted = await mount(canvas, bytes, { onSignal: heard });
        if (hasEnded()) mounted.dispose();
        else handle = mounted;
    }

    run().catch(() => {
        end(AGENT_FIGURE.ENDED);
    });
    return () => {
        end(null);
    };
}

export function install(core: FeatureCore): Promise<void> {
    const drawing: FigureDrawing = import("overnightworks-figure/draw");
    // A failed import is reported by each figure that awaits it; one that fails before the first drawer awaits it is no unhandled rejection.
    drawing.catch(() => undefined);
    const draws: AgentFigureDrawer = (host, path, show) => drawFigure(host, path, show, drawing);
    core.drawAgentFigures(draws);
    return Promise.resolve();
}
