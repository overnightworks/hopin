import { beforeEach, describe, expect, it, vi } from "vitest";
import { createHearingCapture } from "./hearing.ts";
import { HEARING_CLOSE } from "./wire.ts";
import { HopClose } from "overnightworks-speech-browser";

interface FakeCapture {
    options: { stream: MediaStream; hopUrl: string; workletUrl: string };
    stopped: boolean;
    over: boolean;
    end(end: unknown): void;
}

const running = vi.hoisted(() => ({ captures: [] as FakeCapture[] }));

vi.mock("overnightworks-speech-browser", async (importActual) => ({
    ...(await importActual<typeof import("overnightworks-speech-browser")>()),
    startCapture: (options: FakeCapture["options"]) => {
        const ended = Promise.withResolvers<unknown>();
        const capture = {
            options,
            stopped: false,
            over: false,
            ended: ended.promise,
            stop() {
                this.stopped = true;
            },
            end: ended.resolve,
        };
        running.captures.push(capture);
        return capture;
    },
}));

const stream = (name: string) => name as unknown as MediaStream;
const hopUrl = () => "ws://page/hop";

function freshCapture() {
    return createHearingCapture(hopUrl);
}

const live = () => running.captures.filter((capture) => !capture.stopped && !capture.over);

async function endedBy(capture: FakeCapture, end: unknown) {
    capture.over = true;
    capture.end(end);
    await Promise.resolve();
    await Promise.resolve();
}

beforeEach(() => {
    running.captures.length = 0;
});

describe("capturing the sponsor's microphone", () => {
    it.each([
        { heard: false, microphone: false },
        { heard: true, microphone: false },
        { heard: false, microphone: true },
    ])("runs no capture while the switch is $heard and the microphone is $microphone", ({ heard, microphone }) => {
        const capture = freshCapture();

        capture.setHeard(heard);
        capture.setStream(microphone ? stream("microphone") : null);

        expect(running.captures).toHaveLength(0);
    });

    it.each([
        ["the switch comes last", "stream first"],
        ["the microphone comes last", "heard first"],
    ])("starts one capture of the microphone's stream into the seat's hop when %s", (order) => {
        const capture = freshCapture();

        if (order === "stream first") {
            capture.setStream(stream("microphone"));
            capture.setHeard(true);
        } else {
            capture.setHeard(true);
            capture.setStream(stream("microphone"));
        }

        expect(live().map((started) => started.options)).toEqual([
            {
                stream: "microphone",
                hopUrl: "ws://page/hop",
                workletUrl: expect.stringContaining("capture-worklet") as string,
            },
        ]);
    });

    it("starts the hop's capture once however often it is told the same", () => {
        const capture = freshCapture();

        capture.setHeard(true);
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        capture.setStream(stream("microphone"));

        expect(running.captures).toHaveLength(1);
    });

    it("stops the capture the moment the switch goes off, and starts a new one when it is on again", () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        capture.setHeard(false);
        expect(live()).toHaveLength(0);
        capture.setHeard(true);

        expect(live()).toHaveLength(1);
        expect(running.captures).toHaveLength(2);
    });

    it("stops the capture the moment the microphone goes away, and starts a new one with the next stream", () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        capture.setStream(null);
        expect(live()).toHaveLength(0);
        capture.setStream(stream("second"));

        expect(live().map((started) => started.options.stream)).toEqual(["second"]);
    });

    it("moves to a replaced stream by stopping the first capture", () => {
        const capture = freshCapture();
        capture.setHeard(true);
        capture.setStream(stream("first"));

        capture.setStream(stream("second"));

        expect(running.captures.map((started) => [started.options.stream, started.stopped])).toEqual([
            ["first", true],
            ["second", false],
        ]);
    });
});

describe("a hop that ended", () => {
    it("is opened again once when it ended past its deadline while both are still on", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await endedBy(running.captures[0] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });

        expect(live()).toHaveLength(1);
        expect(running.captures).toHaveLength(2);
    });

    it("is not opened a second time when the reopened hop ends past its deadline too", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await endedBy(running.captures[0] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });
        await endedBy(running.captures[1] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });

        expect(live()).toHaveLength(0);
        expect(running.captures).toHaveLength(2);
    });

    it("gets a new reopening after the person's next switch change", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        await endedBy(running.captures[0] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });
        await endedBy(running.captures[1] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });

        capture.setHeard(false);
        capture.setHeard(true);
        await endedBy(running.captures[2] as FakeCapture, { kind: "hop", close: HopClose.PAST_DEADLINE });

        expect(live()).toHaveLength(1);
        expect(running.captures).toHaveLength(4);
    });

    it.each([
        { kind: "hop", close: HopClose.BUSY },
        { kind: "hop", close: HopClose.SERVICE_FAILED },
        { kind: "hop", close: HopClose.NORMAL },
        { kind: "socket", code: HEARING_CLOSE.NO_TOKEN },
        { kind: "local", failure: "worklet-refused" },
    ])("stays closed on its own after %o and starts again with the next change", async (end) => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await endedBy(running.captures[0] as FakeCapture, end);
        expect(running.captures).toHaveLength(1);
        capture.setStream(stream("second"));

        expect(live().map((started) => started.options.stream)).toEqual(["second"]);
    });

    it("is not opened again when it ended past its deadline after the switch went off", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        const first = running.captures[0] as FakeCapture;

        capture.setHeard(false);
        await endedBy(first, { kind: "hop", close: HopClose.PAST_DEADLINE });

        expect(running.captures).toHaveLength(1);
    });

    it("is not mistaken for the running capture when an older one ends late", async () => {
        const capture = freshCapture();
        capture.setStream(stream("first"));
        capture.setHeard(true);
        const first = running.captures[0] as FakeCapture;
        capture.setStream(stream("second"));

        await endedBy(first, { kind: "hop", close: HopClose.PAST_DEADLINE });

        expect(live().map((started) => started.options.stream)).toEqual(["second"]);
        expect(running.captures).toHaveLength(2);
    });
});
