// @vitest-environment jsdom
import { flushSync, mount, unmount } from "svelte";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AGENT_ACTIVITY, type AgentTile, type FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import type { CaptureOptions } from "overnightworks-speech-browser";
import { fakeCaptures } from "./capture.fixture.ts";
import { install, installWith } from "./index.ts";
import { fakePlayers } from "./player.fixture.ts";
import { refusalOf } from "./offers.svelte.ts";
import { AGENT_REFUSAL, HEARING_PATH, INVITATION_OUTCOME, PARTICIPATION_SIGNAL, VOICE_PATH } from "./wire.ts";

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

async function installedParticipation(installOn?: typeof install) {
    const players = fakePlayers();
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
    await (installOn ?? ((on) => installWith(on, players.createPlayer)))(core);
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
        players,
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
        sentence: () =>
            onThePage()
                .flatMap((item) => [...item.querySelectorAll("p")])
                .at(0)?.textContent,
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME, "a2"));
        record(page, true, "a1");
        page.hear(MICROPHONE);
        page.tiles.get("agent-a1")?.hearing?.onChange(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a2" });

        expect([captures.started.length, live().length]).toEqual([1, 0]);
    });

    it("CALL-59: stops the capture on a press of one switch before the server answers and resumes it for the agent that still hears", async () => {
        const page = await sponsorsAgent("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME, "a2"));
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));

        expect([live(), captures.started]).toEqual([[], [expect.anything()]]);
    });
});

const tileActivities = (page: { tiles: Map<string, AgentTile> }) =>
    [...page.tiles.values()].map(({ activity }) => activity);

describe("the page's installation", () => {
    it("shows an announced agent through the module the page installs", async () => {
        const page = await installedParticipation(install);
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        expect(tileActivities(page)).toEqual([AGENT_ACTIVITY.PRESENT]);
    });
});

describe("an agent's voice", () => {
    async function pageWithAgents(...agentIds: string[]) {
        const page = await installedParticipation();
        page.seat(false);
        for (const agentId of agentIds) page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", agentId));
        return page;
    }

    const voiceOf = (agentId: string, voiceId: string) => ({ agent_id: agentId, voice_id: voiceId });

    it("CALL-60: plays each voice through the player of its agent, in the order the frames arrive", async () => {
        const page = await pageWithAgents("a1");

        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "second"));

        expect(page.players.created.map((player) => player.sounds.map(({ sound }) => sound))).toEqual([
            [`${VOICE_PATH}/first`, `${VOICE_PATH}/second`],
        ]);
    });

    it("CALL-60: shows the agent speaking from the start of its sound to the end of it, with the tile's other fields kept", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        const [sound] = page.players.created[0]?.sounds ?? [];

        const before = tileActivities(page);
        await sound?.start();
        const whilePlaying = [...page.tiles.values()].map(({ activity, name, sponsor }) => [activity, name, sponsor]);
        await sound?.end({ kind: "played" });

        expect([before, whilePlaying, tileActivities(page)]).toEqual([
            [AGENT_ACTIVITY.PRESENT],
            [[AGENT_ACTIVITY.SPEAKING, "Claude", "Ada"]],
            [AGENT_ACTIVITY.PRESENT],
        ]);
    });

    it("CALL-60: shows only the agent whose sound plays as speaking while two agents' sounds overlap", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        await page.players.created[0]?.sounds[0]?.start();
        await page.players.created[1]?.sounds[0]?.start();
        await page.players.created[0]?.sounds[0]?.end({ kind: "played" });

        expect([...page.tiles.entries()].map(([key, { activity }]) => [key, activity])).toEqual([
            ["agent-a1", AGENT_ACTIVITY.PRESENT],
            ["agent-a2", AGENT_ACTIVITY.SPEAKING],
        ]);
    });

    it("CALL-60: shows no error when the browser refuses to play a sound", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        await page.players.created[0]?.sounds[0]?.end({ kind: "refused" });

        expect([page.toasts, tileActivities(page)]).toEqual([[], [AGENT_ACTIVITY.PRESENT]]);
    });

    it("CALL-60: keeps the speaking tile when its agent is announced again to the same seat", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        await page.players.created[0]?.sounds[0]?.start();

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone", "a1"));

        expect(tileActivities(page)).toEqual([AGENT_ACTIVITY.SPEAKING]);
    });

    it("CALL-60: closes the player of an agent that leaves and plays no later voice of it", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "late"));

        expect(page.players.created.map((player) => [player.closed, player.sounds.length])).toEqual([
            [true, 1],
            [false, 1],
        ]);
    });

    it("CALL-60: plays no voice of an agent that was never announced", async () => {
        const page = await pageWithAgents();

        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        expect(page.players.created).toEqual([]);
    });

    it("CALL-60: closes every player when the call ends", async () => {
        const page = await pageWithAgents("a1", "a2");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a2", "second"));

        page.endCall();

        expect(page.players.created.map((player) => player.closed)).toEqual([true, true]);
    });

    it("CALL-60: closes the players of the agents a new seat is not told of", async () => {
        const page = await pageWithAgents("a1");
        page.emit(PARTICIPATION_SIGNAL.AGENT_VOICE, voiceOf("a1", "first"));

        page.seat(false, SIGNAL_TYPE.EXISTING_PARTICIPANTS);

        expect(page.players.created.map((player) => player.closed)).toEqual([true]);
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

    it("CALL-52: shows an invitation that ran out as the kit's no-answer row, with no call button", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        expect([page.offers()[0]?.textContent, page.buttons()]).toEqual([expect.stringContaining("Keine Antwort"), []]);
    });

    it("CALL-52: keeps an agent's no-answer row while the list still offers it unchanged", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).toContain("Keine Antwort");
    });

    it("CALL-52: shows a new call of the agent as inviting again, not as its earlier outcome", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-52: shows no earlier outcome of an agent that returns to the list after leaving it", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-52: takes an outcome away with the control at a fresh join", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-51: keeps the control with its one sentence while no agent is listed, and shows none when it is not shown", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        const whileEmpty = page.shown();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        expect([whileEmpty, page.shown()]).toEqual([true, false]);
    });

    it("CALL-51: says that no place is free, in place of the empty sentence, when the frame tells the call is full", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: false, offers: [] });
        const whileFree = page.sentence();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: true, offers: [] });
        const whileFull = page.sentence();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: false, offers: [] });

        expect([whileFree, whileFull, page.sentence()]).toEqual([
            expect.stringContaining("kein Agent erreichbar"),
            expect.stringContaining("kein Platz"),
            whileFree,
        ]);
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
