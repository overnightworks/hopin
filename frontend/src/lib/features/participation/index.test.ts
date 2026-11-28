// @vitest-environment jsdom
import { mount, unmount } from "svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AGENT_ACTIVITY } from "#lib/call/feature-port.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import type { CaptureOptions } from "overnightworks-speech-browser";
import { fakeCaptures } from "./capture.fixture.ts";
import { install } from "./index.ts";
import { ME, installedParticipation, joined, tileActivities } from "./page.fixture.ts";
import { FIGURE_END, HEARING_PATH, PARTICIPATION_SIGNAL, TURN_OUTCOME } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

const captures = fakeCaptures();

// `install(core)` has no seam for the capture, so the package's `startCapture` is the fake one.
vi.mock("overnightworks-speech-browser", async (importActual) => ({
    ...(await importActual<typeof import("overnightworks-speech-browser")>()),
    startCapture: (options: CaptureOptions) => captures.startCapture(options),
}));

describe("dialed-in agents", () => {
    it("CALL-50: shows the tile under the name its sponsor gave, with the sponsor's name", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        expect([...page.tiles.values()].map(({ name, sponsor, activity }) => [name, sponsor, activity])).toEqual([
            ["Claude", "Ada", AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it("CALL-56: hands the path of an agent's figure to its tile, and none for an agent without one", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined(),
            figure: "/api/participation/figures/abc",
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));

        expect([...page.tiles.values()].map(({ figure }) => figure)).toEqual([
            "/api/participation/figures/abc",
            undefined,
        ]);
    });

    it("CALL-65: hands the paths of an agent's clips to its tile, and none for an agent without", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined(),
            figure: "/api/participation/figures/abc",
            gestures: { wave: "/api/participation/clips/def" },
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));

        expect([...page.tiles.values()].map(({ gestures }) => gestures)).toEqual([
            { wave: "/api/participation/clips/def" },
            undefined,
        ]);
    });

    it("CALL-80: hands the path of an agent's room to its tile, and none for an agent without", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined(),
            figure: "/api/participation/figures/abc",
            room: "/api/participation/persona-rooms/ghi",
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));

        expect([...page.tiles.values()].map(({ room }) => room)).toEqual([
            "/api/participation/persona-rooms/ghi",
            undefined,
        ]);
    });

    it("CALL-56: reports the closed code a figure ended with, with the id of its agent", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, {
            ...joined(),
            figure: "/api/participation/figures/abc",
        });
        const [key] = [...page.tiles.keys()];

        page.endFigure(key as string, FIGURE_END.SLOW);

        expect(page.sent).toEqual([{ type: PARTICIPATION_SIGNAL.AGENT_FIGURE_ENDED, agent_id: "a1", code: "slow" }]);
    });

    it("CALL-56: reports nothing for a tile no agent of the call holds", async () => {
        const page = await installedParticipation();
        page.seat(false);

        page.endFigure("agent-gone", FIGURE_END.LOST);

        expect(page.sent).toEqual([]);
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

    it("names the agents it shows to the chat, which sends each line for them through the page's frame", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));
        page.lineReaders().send("hello");

        expect(page.lineReaders().names).toEqual(["Claude", "Claude"]);
        expect(page.sent).toEqual([{ type: PARTICIPATION_SIGNAL.LINE_FOR_AGENTS, text: "hello" }]);
    });

    it("names no agent to the chat once the last one left", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect(page.lineReaders().names).toEqual([]);
    });

    it("takes the tile away when its agent leaves, and shows a repeated join once", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        const whileIn = page.tiles.size;

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect([whileIn, page.tiles.size]).toEqual([1, 0]);
    });

    it("takes every tile away when the call ends", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));

        page.endCall();

        expect(page.tiles.size).toBe(0);
    });

    it.each([SIGNAL_TYPE.ROOM_CREATED, SIGNAL_TYPE.EXISTING_PARTICIPANTS])(
        "takes the tile of an agent that is not announced again away on a fresh join (%s)",
        async (signal) => {
            const page = await installedParticipation();
            page.seat(false);
            page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("gone"));

            page.seat(false, signal);

            expect(page.tiles.size).toBe(0);
        },
    );

    it("CALL-84: shows a re-announced agent again, listed as here only once the new seat's offers frame names it", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [], send_away: ["a1", "a2"] });

        page.emit(SIGNAL_TYPE.EXISTING_PARTICIPANTS, { participant_id: "new-seat", is_host: false });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        const beforeItsFrame = page.offers().length;
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [], send_away: ["a1"] });
        page.buttons()[0]?.click();

        expect([[...page.tiles.keys()], beforeItsFrame, page.offers().length, page.sent]).toEqual([
            ["agent-a1"],
            0,
            1,
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, { ...joined(), figure: "/api/participation/figures/abc" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        const tile = page.tiles.get("agent-a1");
        expect([tile?.name, tile?.sponsor, tile?.figure, tile?.turnState]).toEqual([
            "Claude",
            "Ada",
            "/api/participation/figures/abc",
            "busy",
        ]);
    });

    it("CALL-57: sends the new value on a press and shows what the server answers, never its own flip", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        expect([hearingOf(page, "a1")?.on, hearingOf(page, "a2")]).toEqual([true, undefined]);
    });

    it("CALL-57: keeps the record when the same agent is announced again", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        expect(hearingOf(page)?.on).toBe(true);
    });

    it("CALL-57: shows no switch for an agent that is no longer on the stage", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        expect(page.tiles.size).toBe(0);
    });

    it("CALL-57: shows no switch on an agent announced again to a new seat until that seat's record comes", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        expect(hearingOf(page)).toBeUndefined();
    });
});

describe("the sponsor's microphone capture", () => {
    const MICROPHONE = "microphone" as unknown as MediaStream;
    const sponsorsAgent = async (agentId = "a1") => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(agentId));
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

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

    it("CALL-59: starts no capture on the person's own press of the switch on, only when the server's record says on", async () => {
        const page = await sponsorsAgent();
        record(page, false);
        page.hear(MICROPHONE);

        page.tiles.get("agent-a1")?.hearing?.onChange(true);
        const startedByThePress = captures.started.length;
        record(page, true);

        expect([page.sent, startedByThePress, live().length]).toEqual([
            [{ type: PARTICIPATION_SIGNAL.AGENT_HEARING, agent_id: "a1", on: true }],
            0,
            1,
        ]);
    });

    it("CALL-59: keeps a pressed-off switch's capture stopped when another agent leaves before the server answers", async () => {
        const page = await sponsorsAgent("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));
        record(page, true, "a1");
        page.hear(MICROPHONE);
        page.tiles.get("agent-a1")?.hearing?.onChange(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a2" });

        expect([captures.started.length, live().length]).toEqual([1, 0]);
    });

    it("CALL-59: stops the capture on a press of one switch before the server answers and resumes it for the agent that still hears", async () => {
        const page = await sponsorsAgent("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));
        record(page, true, "a1");
        record(page, true, "a2");
        page.hear(MICROPHONE);

        page.tiles.get("agent-a1")?.hearing?.onChange(false);
        const beforeTheAnswer = live().length;
        record(page, false, "a1");
        const afterTheAnswer = live().length;
        record(page, false, "a2");

        expect([beforeTheAnswer, afterTheAnswer, live().length]).toEqual([0, 1, 0]);
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        expect([live(), captures.started]).toEqual([[], [expect.anything()]]);
    });
});

describe("the page's installation", () => {
    it("shows an announced agent through the module the page installs", async () => {
        const page = await installedParticipation(install);
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        expect(tileActivities(page)).toEqual([AGENT_ACTIVITY.PRESENT]);
    });
});
