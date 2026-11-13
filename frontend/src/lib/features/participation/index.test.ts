// @vitest-environment jsdom
import { flushSync, mount, unmount } from "svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AGENT_ACTIVITY, type AgentTile, type FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import type { CaptureOptions } from "overnightworks-speech-browser";
import { fakeCaptures } from "./capture.fixture.ts";
import { install } from "./index.ts";
import { refusalOf } from "./offers.svelte.ts";
import { AGENT_REFUSAL, HEARING_PATH, PARTICIPATION_SIGNAL } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

const ME = "me";

const captures = fakeCaptures();

// `install(core)` has no seam for the capture, so the package's `startCapture` is the fake one.
vi.mock("overnightworks-speech-browser", async (importActual) => ({
    ...(await importActual<typeof import("overnightworks-speech-browser")>()),
    startCapture: (options: CaptureOptions) => captures.startCapture(options),
}));

async function installedParticipation() {
    const handlers = new Map<string, (frame: never) => void>();
    const sent: unknown[] = [];
    const tiles = new Map<string, AgentTile>();
    const toasts: string[] = [];
    const lines: FeatureView[] = [];
    const moreItems: Element[] = [];
    const callEndedHooks: (() => void)[] = [];
    const microphoneHandlers: ((stream: MediaStream | null) => void)[] = [];
    const more = document.createElement("dialog");
    more.open = true;
    document.body.append(more);
    const core: FeatureCore = {
        on: (type, handler) => void handlers.set(type, handler),
        send: (frame) => void sent.push(frame),
        isConnected: () => true,
        showToast: (text) => void toasts.push(text),
        toastKind: TOAST_KIND,
        showStage: () => undefined,
        drawAgentFigures: () => undefined,
        closeMore: () => undefined,
        showAgentTile: (tile, key = "agent") => {
            if (tile === null) tiles.delete(key);
            else tiles.set(key, tile);
        },
        addMoreItems: (...items) => {
            moreItems.push(...items);
            more.append(...items);
        },
        receiveChatLine: (view: FeatureView) => void lines.push(view),
        pinLastChatLine: () => undefined,
        showChatCard: () => undefined,
        onMicrophone: (handler) => {
            microphoneHandlers.push(handler);
            handler(null);
        },
        onCallEnded: (hook) => void callEndedHooks.push(hook),
    };
    await install(core);
    // What the page draws follows its state at the next flush.
    const onThePage = () => {
        flushSync();
        return moreItems;
    };
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    return {
        emit,
        sent,
        tiles,
        toasts,
        lines,
        offers: () => onThePage().flatMap((item) => [...item.querySelectorAll("li")]),
        buttons: () => onThePage().flatMap((item) => [...item.querySelectorAll("button")]),
        alert: () =>
            onThePage()
                .flatMap((item) => [...item.querySelectorAll("[role=alert]")])
                .at(0)?.textContent,
        shown: () => onThePage().some((item) => item.textContent !== ""),
        focused: () => moreItems.find((item) => item.contains(document.activeElement)),
        endCall: () => {
            callEndedHooks.forEach((hook) => {
                hook();
            });
        },
        hear: (stream: MediaStream | null) => {
            microphoneHandlers.forEach((handler) => {
                handler(stream);
            });
        },
        seat: (isHost: boolean, signal: string = SIGNAL_TYPE.ROOM_CREATED) => {
            emit(signal, { participant_id: ME, is_host: isHost });
        },
    };
}

const joined = (sponsorId: string, agentId = "a1") => ({
    agent_id: agentId,
    name: "Claude",
    sponsor: "Ada",
    sponsor_id: sponsorId,
});

describe("dialed-in agents", () => {
    it("CALL-50: shows the tile under the name its sponsor gave, with the sponsor's name", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect([...page.tiles.values()].map(({ name, sponsor, activity }) => [name, sponsor, activity])).toEqual([
            ["Claude", "Ada", AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it("CALL-56: hands the path of an agent's figure to its tile, and none for an agent without one", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined("someone"),
            figure: "/api/participation/figures/abc",
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a2"));

        expect([...page.tiles.values()].map(({ figure }) => figure)).toEqual([
            "/api/participation/figures/abc",
            undefined,
        ]);
    });

    it.each([
        { who: "the sponsor", host: false, sponsorId: ME },
        { who: "the host", host: true, sponsorId: "someone" },
    ])("CALL-49: offers the remove action to $who, and it asks the server to remove the agent", async (arrival) => {
        const page = await installedParticipation();
        page.seat(arrival.host, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(arrival.sponsorId));

        page.tiles.get("agent-a1")?.onRemove?.();

        expect(page.sent).toEqual([{ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: "a1" }]);
    });

    it("CALL-49: offers no remove action to anyone else", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect(page.tiles.get("agent-a1")?.onRemove).toBeUndefined();
    });

    it("CALL-49: tells a person whose removal was refused that only the sponsor or the host removes the agent", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.NOT_SPONSOR_OR_HOST });

        expect([page.toasts.length, page.alert()]).toEqual([1, undefined]);
    });

    it("shows an agent's line in the chat under its name", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_LINE, { agent_id: "a1", name: "Claude", text: "Hello <b>x</b>" });

        const target = document.createElement("div");
        const [view] = page.lines;
        if (view === undefined) throw new Error("no line was received");
        const mounted = mount(view, { target });
        expect(target.querySelector(".chat-sender")?.textContent).toBe("ClaudeAI");
        expect(target.querySelector(".chat-msg")?.textContent).toBe("ClaudeAIHello <b>x</b>");
        expect(target.querySelector("b")).toBeNull();
        void unmount(mounted);
    });

    it("takes the tile away when its agent leaves, and shows a repeated join once", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));
        const whileIn = page.tiles.size;

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect([whileIn, page.tiles.size]).toEqual([1, 0]);
    });

    it("takes every tile away when the call ends", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a2"));

        page.endCall();

        expect(page.tiles.size).toBe(0);
    });

    it.each([SIGNAL_TYPE.ROOM_CREATED, SIGNAL_TYPE.EXISTING_PARTICIPANTS])(
        "takes the tile of an agent that is not announced again away on a fresh join (%s)",
        async (signal) => {
            const page = await installedParticipation();
            page.seat(false);
            page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "gone"));

            page.seat(false, signal);

            expect(page.tiles.size).toBe(0);
        },
    );

    it("shows a re-announced agent again with the new seat's remove right", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a2"));

        page.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: "new-seat", is_host: false });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("new-seat", "a1"));
        page.tiles.get("agent-a1")?.onRemove?.();

        expect([[...page.tiles.keys()], page.sent]).toEqual([
            ["agent-a1"],
            [{ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: "a1" }],
        ]);
    });
});

describe("the sponsor's hearing switch on an agent's tile", () => {
    const hearingOf = (page: Awaited<ReturnType<typeof installedParticipation>>, agentId = "a1") =>
        page.tiles.get(`agent-${agentId}`)?.hearing;

    it("CALL-57: keeps the tile's other fields when the record arrives", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, { ...joined(ME), figure: "/api/participation/figures/abc" });

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        const tile = page.tiles.get("agent-a1");
        expect([tile?.name, tile?.sponsor, tile?.figure, typeof tile?.onRemove]).toEqual([
            "Claude",
            "Ada",
            "/api/participation/figures/abc",
            "function",
        ]);
    });

    it("CALL-57: sends the new value on a press and shows what the server answers, never its own flip", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        hearingOf(page)?.onChange(false);
        const beforeTheAnswer = hearingOf(page)?.on;
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: false });

        expect([page.sent, beforeTheAnswer, hearingOf(page)?.on]).toEqual([
            [{ type: PARTICIPATION_SIGNAL.AGENT_HEARING, agent_id: "a1", on: false }],
            true,
            false,
        ]);
    });

    it("CALL-57: gives each agent its own record", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME, "a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a2"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        expect([hearingOf(page, "a1")?.on, hearingOf(page, "a2")]).toEqual([true, undefined]);
    });

    it("CALL-57: keeps the record when the same agent is announced again", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));

        expect(hearingOf(page)?.on).toBe(true);
    });

    it("CALL-57: shows no switch for an agent that is no longer on the stage", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));
        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        expect(page.tiles.size).toBe(0);
    });

    it("CALL-57: shows no switch on an agent announced again to a new seat until that seat's record comes", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect(hearingOf(page)).toBeUndefined();
    });
});

describe("the sponsor's microphone capture", () => {
    const MICROPHONE = "microphone" as unknown as MediaStream;
    const sponsorsAgent = async (agentId = "a1") => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME, agentId));
        return page;
    };
    const record = (page: Awaited<ReturnType<typeof installedParticipation>>, on: boolean, agentId = "a1") => {
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: agentId, on });
    };
    const { live } = captures;

    beforeEach(captures.clear);

    it("CALL-59: captures the microphone into the hop of the seat while the record is on and the microphone is held", async () => {
        const page = await sponsorsAgent();

        record(page, true);
        page.hear(MICROPHONE);

        expect(live().map((capture) => [capture.options.stream, capture.options.hopUrl])).toEqual([
            [MICROPHONE, `ws://${location.host}${HEARING_PATH}/${ME}`],
        ]);
    });

    it("CALL-59: starts no capture for a seat that gets no record, however its microphone is", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        page.hear(MICROPHONE);

        expect(captures.started).toHaveLength(0);
    });

    it("CALL-59: starts no capture while the record is off or the microphone is not held", async () => {
        const page = await sponsorsAgent();
        record(page, false);
        page.hear(MICROPHONE);
        page.hear(null);
        record(page, true);

        expect(captures.started).toHaveLength(0);
    });

    it("CALL-59: stops the capture on the person's own press of the switch, before the server answers", async () => {
        const page = await sponsorsAgent();
        record(page, true);
        page.hear(MICROPHONE);

        page.tiles.get("agent-a1")?.hearing?.onChange(false);

        expect(live()).toHaveLength(0);
    });

    it("CALL-59: starts a capture again when the server answers a press of the switch off with the record still on", async () => {
        const page = await sponsorsAgent();
        record(page, true);
        page.hear(MICROPHONE);
        page.tiles.get("agent-a1")?.hearing?.onChange(false);

        record(page, true);

        expect(live()).toHaveLength(1);
    });

    it("CALL-59: keeps capturing for another agent that still hears the person when one is switched off", async () => {
        const page = await sponsorsAgent("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME, "a2"));
        record(page, true, "a1");
        record(page, true, "a2");
        page.hear(MICROPHONE);

        page.tiles.get("agent-a1")?.hearing?.onChange(false);
        record(page, false, "a1");
        expect(live()).toHaveLength(1);
        record(page, false, "a2");

        expect(live()).toHaveLength(0);
    });

    it("CALL-59: stops the capture when the agent that hears the person leaves", async () => {
        const page = await sponsorsAgent();
        record(page, true);
        page.hear(MICROPHONE);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect(live()).toHaveLength(0);
    });

    it("CALL-59: stops the capture when the call ends", async () => {
        const page = await sponsorsAgent();
        record(page, true);
        page.hear(MICROPHONE);

        page.endCall();

        expect(live()).toHaveLength(0);
    });

    it("CALL-59: stops the capture when a new seat is announced, and starts none until that seat's record", async () => {
        const page = await sponsorsAgent();
        record(page, true);
        page.hear(MICROPHONE);

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));

        expect([live(), captures.started]).toEqual([[], [expect.anything()]]);
    });
});

describe("calling an agent that has an account of its own", () => {
    const offered = (name: string, pending = false, key = "k1") => ({ agent_key: key, name, pending });
    const names = (page: Awaited<ReturnType<typeof installedParticipation>>) =>
        page.offers().map((row) => row.querySelector(".name")?.textContent);

    it("CALL-51: lists an offered agent per row, and calling one asks the server to add that agent", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, {
            shown: true,
            offers: [offered("Mozart"), offered("Bach", false, "k2")],
        });
        page.buttons()[1]?.click();

        expect([names(page), page.sent]).toEqual([
            ["Mozart", "Bach"],
            [{ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: "k2" }],
        ]);
    });

    it("CALL-51: shows an open invitation as a row with no button, so it cannot be called again", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect([names(page), page.buttons()]).toEqual([["Mozart"], []]);
    });

    it("follows each frame exactly: the rows of the last frame are all there is", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });
        const pending = page.buttons().length;
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });

        expect([pending, names(page)]).toEqual([0, []]);
    });

    it("CALL-51: keeps the control with its one sentence while no agent is listed, and shows none when it is not shown", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        const whileEmpty = page.shown();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        expect([whileEmpty, page.shown()]).toEqual([true, false]);
    });

    it("keeps the agent's name as text, never as markup", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("<b>Mozart</b>")] });

        expect([names(page), page.offers()[0]?.querySelector("b")]).toEqual([["<b>Mozart</b>"], null]);
    });

    it("moves focus to the control when the row whose button had focus turns inviting", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.buttons()[0]?.focus();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect(page.focused()?.id).toBe("agentOffers");
    });

    it.each([
        [AGENT_REFUSAL.NOT_ADDER, "not-allowed"],
        [AGENT_REFUSAL.NO_SEAT, "owner-absent"],
        [AGENT_REFUSAL.SPONSOR_AWAY, "owner-absent"],
        [AGENT_REFUSAL.BUSY, "place-full"],
        [AGENT_REFUSAL.ALREADY_THERE, "agent-unavailable"],
        [AGENT_REFUSAL.GONE, "agent-unavailable"],
        [AGENT_REFUSAL.TWO_SEATS, "agent-unavailable"],
        [AGENT_REFUSAL.UNAVAILABLE, "try-again"],
        ["another_reason", "try-again"],
    ])("CALL-51: maps the refused add %s to the kit's %s reason", (reason, kitReason) => {
        expect(refusalOf(reason)).toBe(kitReason);
    });

    it("CALL-51: shows a refused add as one of the kit's sentences", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        expect(page.alert()).toBeTruthy();
    });

    it("CALL-51: keeps a refusal that follows the offers frame taking the control away", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.NOT_ADDER });

        expect(page.alert()).toBeTruthy();
    });

    it("takes the refusal away when the person calls again", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        page.buttons()[0]?.click();

        expect(page.alert()).toBeUndefined();
    });

    it.each([
        {
            when: "a fresh join",
            end: (page: Awaited<ReturnType<typeof installedParticipation>>) => {
                page.seat(false);
            },
        },
        {
            when: "the end of the call",
            end: (page: Awaited<ReturnType<typeof installedParticipation>>) => {
                page.endCall();
            },
        },
    ])("takes the control and its refusal away at $when", async ({ end }) => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        end(page);

        expect([page.shown(), page.alert()]).toEqual([false, undefined]);
    });
});
