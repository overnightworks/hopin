// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { AGENT_FIGURE, type AgentFigure, type AgentFigureDrawer, type FeatureCore } from "#lib/call/feature-port.ts";
import { FIGURE_END } from "#lib/features/participation/wire.ts";

type Signal = "drawn" | "unsupported" | "refused" | "lost" | "slow";

interface Mounting {
    canvas: HTMLCanvasElement;
    bytes: Uint8Array;
    gestures: Record<string, Uint8Array> | undefined;
    give: (signal: Signal) => void;
    resolve: () => void;
    dispose: ReturnType<typeof vi.fn>;
    gesture: ReturnType<typeof vi.fn>;
    frame: ReturnType<typeof vi.fn>;
    framing: string | undefined;
    room: Uint8Array | undefined;
    handleRoom: ReturnType<typeof vi.fn>;
    listen: ReturnType<typeof vi.fn>;
    speak: ReturnType<typeof vi.fn>;
    think: ReturnType<typeof vi.fn>;
}

// The package draws with WebGL, which jsdom has none of: a fake `mount` stands in and the test decides what it signals and when it resolves.
const drawing = vi.hoisted(() => ({ mounts: [] as Mounting[] }));

function fakeMount(
    canvas: HTMLCanvasElement,
    bytes: Uint8Array,
    options: {
        onSignal: (signal: Signal) => void;
        gestures?: Record<string, Uint8Array>;
        framing?: string;
        room?: Uint8Array;
    },
) {
    return new Promise((resolve) => {
        const dispose = vi.fn();
        const gesture = vi.fn();
        const frame = vi.fn();
        const handleRoom = vi.fn();
        const listen = vi.fn();
        const speak = vi.fn();
        const think = vi.fn();
        drawing.mounts.push({
            canvas,
            bytes,
            gestures: options.gestures,
            framing: options.framing,
            room: options.room,
            handleRoom,
            give: options.onSignal,
            resolve: () => {
                resolve({ signal: "drawn", dispose, gesture, frame, room: handleRoom, listen, speak, think });
            },
            dispose,
            gesture,
            frame,
            listen,
            speak,
            think,
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

function draw(drawer: AgentFigureDrawer, gestures?: Record<string, string>, framing?: string, room?: string) {
    const host = document.createElement("div");
    const shown: AgentFigure[] = [];
    // The reason each report gave, undefined for none.
    const reasons: (string | undefined)[] = [];
    const { stop, gesture, frame, listen, speak, think } = drawer(
        host,
        FIGURE_PATH,
        (figure, reason) => {
            shown.push(figure);
            reasons.push(reason);
        },
        gestures,
        framing,
        room,
    );
    return { host, shown, reasons, stop, gesture, frame, listen, speak, think };
}

// The frontend carries no Node typings; a rejection nothing handles reaches the test through the runtime's process.
const runtime = (globalThis as unknown as { process: UnhandledRejectionSource }).process;

const settled = () => new Promise((resolve) => setTimeout(resolve, 0));

async function mounted(drawer: AgentFigureDrawer, gestures?: Record<string, string>, framing?: string, room?: string) {
    const drawn = draw(drawer, gestures, framing, room);
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

    it.each([
        ["unsupported", FIGURE_END.UNSUPPORTED],
        ["refused", FIGURE_END.REFUSED],
        ["lost", FIGURE_END.LOST],
        ["slow", FIGURE_END.SLOW],
    ] as const)(
        "CALL-56: ends the figure on %s: reports it with that reason, disposes it once and takes the canvas off the page",
        async (signal, reason) => {
            fetchedBytes();
            const { host, shown, reasons, mounting, stop } = await mounted(await installedDrawer());
            mounting.resolve();
            await settled();
            mounting.give("drawn");

            mounting.give(signal);
            stop();
            mounting.give("drawn");

            expect(shown).toEqual([AGENT_FIGURE.DRAWN, AGENT_FIGURE.ENDED]);
            expect(reasons).toEqual([undefined, reason]);
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
        const { host, shown, reasons, stop } = draw(await installedDrawer());
        await vi.waitFor(() => {
            expect(shown).toEqual([AGENT_FIGURE.ENDED]);
        });
        stop();

        expect([drawing.mounts.length, shown, reasons, host.querySelector("canvas")]).toEqual([
            0,
            [AGENT_FIGURE.ENDED],
            [FIGURE_END.UNLOADED],
            null,
        ]);
    });

    it("CALL-56: ends the figure with no canvas when the figure's chunk fails to load", async () => {
        fetchedBytes();
        const { host, shown, reasons } = draw(await installedDrawer(true));
        await vi.waitFor(() => {
            expect(shown).toEqual([AGENT_FIGURE.ENDED]);
        });

        expect([reasons, host.querySelector("canvas")]).toEqual([[FIGURE_END.UNLOADED], null]);
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

    it("CALL-77: takes a framing through the mounted figure's handle", async () => {
        fetchedBytes();
        const { frame, mounting } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();

        frame("fullBody");

        expect(mounting.frame.mock.calls).toEqual([["fullBody"]]);
    });

    it("CALL-78: mounts the figure in the framing the tile carried", async () => {
        fetchedBytes();

        const { mounting } = await mounted(await installedDrawer(), undefined, "upperBody");

        expect(mounting.framing).toBe("upperBody");
    });

    it("CALL-77: shows a framing taken while the figure loads once it is drawn", async () => {
        fetchedBytes();
        const { frame, mounting } = await mounted(await installedDrawer(), undefined, "face");

        frame("fullBody");
        mounting.resolve();
        await settled();

        expect(mounting.frame.mock.calls).toEqual([["fullBody"]]);
    });

    it("CALL-64: drops a gesture while the figure is still loading", async () => {
        fetchedBytes();
        const { gesture, mounting } = await mounted(await installedDrawer());

        gesture("wave");
        mounting.resolve();
        await settled();

        expect(mounting.gesture).not.toHaveBeenCalled();
    });

    it("CALL-66: turns the mounted figure toward each speaker it is given, and toward nobody", async () => {
        fetchedBytes();
        const { listen, mounting } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();
        const speaker = document.createElement("div");

        listen(speaker);
        listen(null);

        expect(mounting.listen.mock.calls).toEqual([[speaker], [null]]);
    });

    it("CALL-66: turns a figure toward the speaker it was given while it loaded once it is drawn", async () => {
        fetchedBytes();
        const { listen, mounting } = await mounted(await installedDrawer());
        const speaker = document.createElement("div");

        listen(speaker);
        mounting.resolve();
        await settled();

        expect(mounting.listen.mock.calls).toEqual([[speaker]]);
    });

    it("CALL-67: hands the mounted figure each sentence with its voice and mood, and its end", async () => {
        fetchedBytes();
        const { speak, mounting } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();
        const voice = {} as AudioNode;

        speak({ voice, mood: "happy" });
        speak({ voice });
        speak(null);

        expect(mounting.speak.mock.calls).toEqual([[{ voice, mood: "happy" }], [{ voice }], [null]]);
    });

    it.each([
        { given: "a sentence", sentence: { voice: {} as AudioNode, mood: "sad" }, told: [[{ mood: "sad" }]] },
        { given: "its end", sentence: null, told: [] },
    ])("CALL-67: hands a figure $given given while it loaded once it is drawn", async ({ sentence, told }) => {
        fetchedBytes();
        const { speak, mounting } = await mounted(await installedDrawer());

        speak(sentence);
        mounting.resolve();
        await settled();

        expect(mounting.speak.mock.calls).toMatchObject(told);
    });

    it("CALL-68: shows through the mounted figure that its agent thinks, and that it stopped", async () => {
        fetchedBytes();
        const { think, mounting } = await mounted(await installedDrawer());
        mounting.resolve();
        await settled();

        think(true);
        think(false);

        expect(mounting.think.mock.calls).toEqual([[true], [false]]);
    });

    it("CALL-68: a figure whose agent began thinking while it loaded thinks once it is drawn", async () => {
        fetchedBytes();
        const { think, mounting } = await mounted(await installedDrawer());

        think(true);
        mounting.resolve();
        await settled();

        expect(mounting.think.mock.calls).toEqual([[true]]);
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

describe("a persona's room behind an agent's figure", () => {
    const ROOM_PATH = "/api/participation/persona-rooms/ghi";
    const ROOM_BYTES = new Uint8Array([5, 6, 7]);

    // A fetch the test answers when it chooses; the room's signal is kept to see whether the page stops the fetch.
    function held() {
        let release: () => void = () => undefined;
        const gate = new Promise<void>((resolve) => {
            release = resolve;
        });
        return { gate, release };
    }

    function fetchedRoom(
        room: (init?: RequestInit) => Promise<Response>,
        figureGate: Promise<void> = Promise.resolve(),
    ) {
        const roomSignals: (AbortSignal | null | undefined)[] = [];
        vi.stubGlobal(
            "fetch",
            vi.fn((path: unknown, init?: RequestInit) => {
                if (path === FIGURE_PATH) return figureGate.then(() => new Response(BYTES));
                roomSignals.push(init?.signal);
                return room(init);
            }),
        );
        return roomSignals;
    }

    const roomServed = () => Promise.resolve(new Response(ROOM_BYTES));
    const roomPending = (init?: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => {
                reject(new DOMException("aborted", "AbortError"));
            });
        });

    it("CALL-80: mounts the figure with the room's bytes when they arrive before mount is called", async () => {
        const figure = held();
        fetchedRoom(roomServed, figure.gate);
        const drawer = await installedDrawer();
        draw(drawer, undefined, undefined, ROOM_PATH);
        await settled();
        figure.release();
        await vi.waitFor(() => {
            expect(drawing.mounts).toHaveLength(1);
        });
        const [mounting] = drawing.mounts;
        mounting?.resolve();
        await settled();

        expect([[...(mounting?.room ?? [])], mounting?.handleRoom.mock.calls]).toEqual([[...ROOM_BYTES], []]);
    });

    it("CALL-80: hands the handle the room's bytes that arrive while mount is awaited, once it resolves", async () => {
        const arrival = held();
        fetchedRoom(() => arrival.gate.then(roomServed));
        const { mounting } = await mounted(await installedDrawer(), undefined, undefined, ROOM_PATH);

        arrival.release();
        await settled();
        mounting.resolve();
        await settled();

        expect([mounting.room, mounting.handleRoom.mock.calls.map(([bytes]) => [...(bytes as Uint8Array)])]).toEqual([
            undefined,
            [[...ROOM_BYTES]],
        ]);
    });

    it("CALL-80: hands the handle the room's bytes that arrive after the figure is drawn", async () => {
        const arrival = held();
        fetchedRoom(() => arrival.gate.then(roomServed));
        const { mounting } = await mounted(await installedDrawer(), undefined, undefined, ROOM_PATH);
        mounting.resolve();
        await settled();
        expect(mounting.handleRoom).not.toHaveBeenCalled();

        arrival.release();
        await settled();

        expect(mounting.handleRoom.mock.calls.map(([bytes]) => [...(bytes as Uint8Array)])).toEqual([[...ROOM_BYTES]]);
    });

    it("CALL-80: mounts the figure while the room's fetch is still pending", async () => {
        fetchedRoom(roomPending);

        const { mounting } = await mounted(await installedDrawer(), undefined, undefined, ROOM_PATH);

        expect(mounting.room).toBeUndefined();
    });

    it.each([
        {
            ending: "the figure ends",
            end: (_figure: { stop: () => void }, mounting: Mounting) => {
                mounting.give("lost");
            },
        },
        {
            ending: "the tile stops it",
            end: (figure: { stop: () => void }) => {
                figure.stop();
            },
        },
    ])(
        "CALL-80: aborts the room's fetch when $ending while it is pending, and hands the figure no room",
        async ({ end }) => {
            const roomSignals = fetchedRoom(roomPending);
            const drawn = await mounted(await installedDrawer(), undefined, undefined, ROOM_PATH);

            end(drawn, drawn.mounting);
            drawn.mounting.resolve();
            await settled();

            expect([roomSignals[0]?.aborted, drawn.mounting.room, drawn.mounting.handleRoom.mock.calls]).toEqual([
                true,
                undefined,
                [],
            ]);
        },
    );

    it("CALL-80: draws the figure with no room and no end signal when the room cannot be fetched", async () => {
        fetchedRoom(() => Promise.resolve(new Response("no", { status: 502 })));

        const { mounting, shown } = await mounted(await installedDrawer(), undefined, undefined, ROOM_PATH);
        mounting.resolve();
        mounting.give("drawn");
        await settled();

        expect([mounting.room, mounting.handleRoom.mock.calls, shown]).toEqual([undefined, [], [AGENT_FIGURE.DRAWN]]);
    });

    it("CALL-80: fetches no room for a figure that is seen in none", async () => {
        const fetchStub = fetchedBytes();

        await mounted(await installedDrawer());

        expect(fetchStub.mock.calls.map(([path]) => path)).toEqual([FIGURE_PATH]);
    });
});
