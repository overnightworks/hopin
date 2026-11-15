// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { AGENT_FIGURE, type AgentFigure, type AgentFigureDrawer, type FeatureCore } from "#lib/call/feature-port.ts";

type Signal = "drawn" | "unsupported" | "refused" | "lost" | "slow";

interface Mounting {
    canvas: HTMLCanvasElement;
    bytes: Uint8Array;
    gestures: Record<string, Uint8Array> | undefined;
    give: (signal: Signal) => void;
    resolve: () => void;
    dispose: ReturnType<typeof vi.fn>;
    gesture: ReturnType<typeof vi.fn>;
}

// The package draws with WebGL, which jsdom has none of: a fake `mount` stands in and the test decides what it signals and when it resolves.
const drawing = vi.hoisted(() => ({ mounts: [] as Mounting[] }));

function fakeMount(
    canvas: HTMLCanvasElement,
    bytes: Uint8Array,
    options: { onSignal: (signal: Signal) => void; gestures?: Record<string, Uint8Array> },
) {
    return new Promise((resolve) => {
        const dispose = vi.fn();
        const gesture = vi.fn();
        drawing.mounts.push({
            canvas,
            bytes,
            gestures: options.gestures,
            give: options.onSignal,
            resolve: () => {
                resolve({ signal: "drawn", dispose, gesture });
            },
            dispose,
            gesture,
        });
    });
}

interface UnhandledRejectionSource {
    on: (event: "unhandledRejection", listener: (reason: unknown) => void) => void;
    off: (event: "unhandledRejection", listener: (reason: unknown) => void) => void;
}

const FIGURE_PATH = "/api/participation/figures/abc";
const BYTES = new Uint8Array([1, 2, 3]);

afterEach(() => {
    vi.unstubAllGlobals();
    vi.doUnmock("overnightworks-figure/draw");
    drawing.mounts.length = 0;
});

function fetched(answer: () => Promise<Response>) {
    const stub = vi.fn<typeof fetch>(() => answer());
    vi.stubGlobal("fetch", stub);
    return stub;
}

function fetchedBytes() {
    return fetched(() => Promise.resolve(new Response(BYTES)));
}

async function installedDrawer(importFails = false): Promise<AgentFigureDrawer> {
    vi.resetModules();
    vi.doMock("overnightworks-figure/draw", () => {
        if (importFails) throw new Error("the chunk did not load");
        return { mount: fakeMount };
    });
    const { install } = await import("./index.ts");
    let drawer: AgentFigureDrawer | undefined;
    await install({
        drawAgentFigures: (given: AgentFigureDrawer) => {
            drawer = given;
        },
    } as unknown as FeatureCore);
    if (drawer === undefined) throw new Error("install registered no drawer");
    return drawer;
}

function draw(drawer: AgentFigureDrawer, gestures?: Record<string, string>) {
    const host = document.createElement("div");
    const shown: AgentFigure[] = [];
    const { stop, gesture } = drawer(host, FIGURE_PATH, (figure) => void shown.push(figure), gestures);
    return { host, shown, stop, gesture };
}

// The frontend carries no Node typings; a rejection nothing handles reaches the test through the runtime's process.
const runtime = (globalThis as unknown as { process: UnhandledRejectionSource }).process;

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

async function mounted(drawer: AgentFigureDrawer, gestures?: Record<string, string>) {
    const drawn = draw(drawer, gestures);
    await vi.waitFor(() => {
        expect(drawing.mounts).toHaveLength(1);
    });
    const [mounting] = drawing.mounts;
    if (mounting === undefined) throw new Error("nothing was mounted");
    return { ...drawn, mounting };
}

describe("a figure on an agent's tile", () => {
    it("CALL-56: mounts the fetched bytes on a fresh canvas inside the host, sized through its style", async () => {
        const fetchStub = fetchedBytes();
        const { host, mounting } = await mounted(await installedDrawer());

        expect(fetchStub.mock.calls[0]?.[0]).toBe(FIGURE_PATH);
        expect([...host.querySelectorAll("canvas")]).toEqual([mounting.canvas]);
        expect(mounting.canvas.hasAttribute("style")).toBe(true);
        expect(mounting.canvas.style.width).toBe("100%");
        expect(mounting.canvas.style.height).toBe("100%");
        expect([...mounting.bytes]).toEqual([...BYTES]);
    });

    it("CALL-56: draws every figure on a canvas of its own", async () => {
        fetchedBytes();
        const drawer = await installedDrawer();
        draw(drawer);
        draw(drawer);
        await vi.waitFor(() => {
            expect(drawing.mounts).toHaveLength(2);
        });

        const [first, second] = drawing.mounts;

        expect(first?.canvas).not.toBe(second?.canvas);
    });

    it("CALL-56: puts the canvas below the symbol the host already holds", async () => {
        fetchedBytes();
        const drawer = await installedDrawer();
        const host = document.createElement("div");
        const symbol = document.createElement("span");
        host.append(symbol);
        drawer(host, FIGURE_PATH, () => undefined);
        await vi.waitFor(() => {
            expect(drawing.mounts).toHaveLength(1);
        });

        expect(host.firstElementChild).toBe(drawing.mounts[0]?.canvas);
    });

    it("CALL-56: reports drawn once the figure says drawn and keeps the canvas", async () => {
        fetchedBytes();
        const { host, shown, mounting } = await mounted(await installedDrawer());

        mounting.give("drawn");

        expect([shown, host.querySelectorAll("canvas").length]).toEqual([[AGENT_FIGURE.DRAWN], 1]);
    });

    it.each(["unsupported", "refused", "lost", "slow"] as const)(
        "CALL-56: ends the figure on %s: reports it, disposes it once and takes the canvas off the page",
        async (signal) => {
            fetchedBytes();
            const { host, shown, mounting, stop } = await mounted(await installedDrawer());
            mounting.resolve();
            await settled();
            mounting.give("drawn");

            mounting.give(signal);
            stop();
            mounting.give("drawn");

            expect(shown).toEqual([AGENT_FIGURE.DRAWN, AGENT_FIGURE.ENDED]);
            expect(mounting.dispose).toHaveBeenCalledTimes(1);
            expect(host.querySelector("canvas")).toBeNull();
        },
    );

    it("CALL-56: disposes a figure that ended before mount resolved, once, when mount resolves", async () => {
        fetchedBytes();
        const { host, shown, mounting } = await mounted(await installedDrawer());

        mounting.give("unsupported");
        mounting.resolve();
        await settled();

        expect(shown).toEqual([AGENT_FIGURE.ENDED]);
        expect(mounting.dispose).toHaveBeenCalledTimes(1);
        expect(host.querySelector("canvas")).toBeNull();
    });

    it("CALL-56: stop takes the canvas off the page, disposes the figure once and is safe to call twice", async () => {
        fetchedBytes();
        const { host, shown, mounting, stop } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();

        stop();
        stop();
        mounting.give("drawn");
        mounting.give("slow");

        expect(shown).toEqual([]);
        expect(mounting.dispose).toHaveBeenCalledTimes(1);
        expect(host.querySelector("canvas")).toBeNull();
    });

    it("CALL-56: disposes a figure whose mount resolves after stop, with no report", async () => {
        fetchedBytes();
        const { host, shown, mounting, stop } = await mounted(await installedDrawer());

        stop();
        mounting.give("drawn");
        mounting.resolve();
        await settled();

        expect(shown).toEqual([]);
        expect(mounting.dispose).toHaveBeenCalledTimes(1);
        expect(host.querySelector("canvas")).toBeNull();
    });

    it("CALL-56: mounts nothing and creates no canvas when stopped before the bytes are there", async () => {
        let deliver: (response: Response) => void = () => undefined;
        const fetchStub = fetched(
            () =>
                new Promise((resolve) => {
                    deliver = resolve;
                }),
        );
        const { host, shown, stop } = draw(await installedDrawer());
        await vi.waitFor(() => {
            expect(fetchStub).toHaveBeenCalledTimes(1);
        });
        const signal = fetchStub.mock.calls[0]?.[1]?.signal;

        stop();
        deliver(new Response(BYTES));
        await settled();

        expect(signal?.aborted).toBe(true);
        expect([drawing.mounts.length, shown, host.querySelector("canvas")]).toEqual([0, [], null]);
    });

    it.each([
        ["a fetch that fails", () => Promise.reject(new TypeError("offline"))],
        ["a figure the server does not hold", () => Promise.resolve(new Response("no", { status: 404 }))],
    ])("CALL-56: ends the figure with no canvas on %s", async (_name, answer) => {
        fetched(answer);
        const { host, shown, stop } = draw(await installedDrawer());
        await vi.waitFor(() => {
            expect(shown).toEqual([AGENT_FIGURE.ENDED]);
        });
        stop();

        expect([drawing.mounts.length, shown, host.querySelector("canvas")]).toEqual([0, [AGENT_FIGURE.ENDED], null]);
    });

    it("CALL-56: ends the figure with no canvas when the figure's chunk fails to load", async () => {
        fetchedBytes();
        const { host, shown } = draw(await installedDrawer(true));
        await vi.waitFor(() => {
            expect(shown).toEqual([AGENT_FIGURE.ENDED]);
        });

        expect(host.querySelector("canvas")).toBeNull();
    });

    it("CALL-56: a chunk that fails to load is no unhandled rejection before any figure awaits it", async () => {
        const unhandled: unknown[] = [];
        const record = (reason: unknown) => void unhandled.push(reason);
        runtime.on("unhandledRejection", record);
        try {
            await installedDrawer(true);
            await settled();
            await settled();
        } finally {
            runtime.off("unhandledRejection", record);
        }

        expect(unhandled).toEqual([]);
    });

    it("CALL-64: plays a gesture through the mounted figure's handle", async () => {
        fetchedBytes();
        const { gesture, mounting } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();

        gesture("wave");

        expect(mounting.gesture.mock.calls).toEqual([["wave"]]);
    });

    it("CALL-64: drops a gesture while the figure is still loading", async () => {
        fetchedBytes();
        const { gesture, mounting } = await mounted(await installedDrawer());

        gesture("wave");
        mounting.resolve();
        await settled();

        expect(mounting.gesture).not.toHaveBeenCalled();
    });
});

describe("a persona's clips on an agent's figure", () => {
    const WAVE_PATH = "/api/participation/clips/def";
    const CLIP_BYTES = new Uint8Array([9, 8, 7]);

    function fetchedByPath(answers: Record<string, () => Promise<Response>>) {
        const stub = vi.fn<typeof fetch>((input) => {
            const answer = answers[input as string];
            return answer === undefined ? Promise.reject(new TypeError("no answer")) : answer();
        });
        vi.stubGlobal("fetch", stub);
        return stub;
    }

    const served = (bytes: Uint8Array<ArrayBuffer>) => () => Promise.resolve(new Response(bytes));

    it("CALL-65: mounts the figure with the bytes of each clip under the gesture it replaces", async () => {
        const fetchStub = fetchedByPath({ [FIGURE_PATH]: served(BYTES), [WAVE_PATH]: served(CLIP_BYTES) });

        const { mounting } = await mounted(await installedDrawer(), { wave: WAVE_PATH });

        expect(fetchStub.mock.calls.map(([path]) => path).sort()).toEqual([WAVE_PATH, FIGURE_PATH].sort());
        expect(Object.entries(mounting.gestures ?? {}).map(([name, bytes]) => [name, [...bytes]])).toEqual([
            ["wave", [...CLIP_BYTES]],
        ]);
    });

    it("CALL-65: fetches no clip for a figure that brings none", async () => {
        const fetchStub = fetchedBytes();

        const { mounting } = await mounted(await installedDrawer());

        expect([fetchStub.mock.calls.length, Object.keys(mounting.gestures ?? {})]).toEqual([1, []]);
    });

    it.each([
        ["a clip the server does not hold", () => Promise.resolve(new Response("no", { status: 502 }))],
        ["a clip that cannot be fetched", () => Promise.reject(new TypeError("offline"))],
    ])("CALL-65: draws the figure with no clip for %s", async (_name, answer) => {
        fetchedByPath({ [FIGURE_PATH]: served(BYTES), [WAVE_PATH]: answer });

        const { mounting, shown } = await mounted(await installedDrawer(), { wave: WAVE_PATH });
        mounting.give("drawn");

        expect([Object.keys(mounting.gestures ?? {}), shown]).toEqual([[], [AGENT_FIGURE.DRAWN]]);
    });

    it("CALL-65: gives up a clip that takes too long and draws the figure without it", async () => {
        vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
        try {
            const waiting = (_path: unknown, init?: RequestInit) =>
                new Promise<Response>((_resolve, reject) => {
                    init?.signal?.addEventListener("abort", () => {
                        reject(new DOMException("aborted", "AbortError"));
                    });
                });
            vi.stubGlobal(
                "fetch",
                vi.fn((path: unknown, init?: RequestInit) =>
                    path === FIGURE_PATH ? Promise.resolve(new Response(BYTES)) : waiting(path, init),
                ),
            );
            draw(await installedDrawer(), { wave: WAVE_PATH });

            await vi.runAllTimersAsync();
            await vi.waitFor(() => {
                expect(drawing.mounts).toHaveLength(1);
            });

            expect(Object.keys(drawing.mounts[0]?.gestures ?? {})).toEqual([]);
        } finally {
            vi.useRealTimers();
        }
    });
});
