import { beforeEach, describe, expect, it } from "vitest";
import { HopClose } from "overnightworks-speech-browser";
import { fakeCaptures } from "./capture.fixture.ts";
import { createHearingCapture } from "./hearing.ts";
import { HEARING_CLOSE } from "./wire.ts";

const captures = fakeCaptures();
const { live } = captures;

const stream = (name: string) => name as unknown as MediaStream;
const streamsOf = () => live().map((started) => started.options.stream);
const freshCapture = () => createHearingCapture(() => "ws://page/hop", captures.startCapture);
const PAST_DEADLINE = { kind: "hop", close: HopClose.PAST_DEADLINE } as const;

beforeEach(captures.clear);

describe("capturing the sponsor's microphone", () => {
    it.each([
        { heard: false, microphone: false },
        { heard: true, microphone: false },
        { heard: false, microphone: true },
    ])(
        "CALL-59: runs no capture while the switch is $heard and the microphone is $microphone",
        ({ heard, microphone }) => {
            const capture = freshCapture();

            capture.setHeard(heard);
            capture.setStream(microphone ? stream("microphone") : null);

            expect(captures.started).toHaveLength(0);
        },
    );

    it.each([
        { last: "the switch", order: ["stream", "heard"] },
        { last: "the microphone", order: ["heard", "stream"] },
    ] as const)(
        "CALL-59: starts one capture of the microphone's stream into the seat's hop when $last comes last",
        ({ order }) => {
            const capture = freshCapture();
            const arrivals = {
                stream: () => {
                    capture.setStream(stream("microphone"));
                },
                heard: () => {
                    capture.setHeard(true);
                },
            };

            for (const arrival of order) arrivals[arrival]();

            expect(live().map((started) => started.options)).toEqual([
                {
                    stream: "microphone",
                    hopUrl: "ws://page/hop",
                    workletUrl: expect.stringContaining("capture-worklet") as string,
                },
            ]);
        },
    );

    it("CALL-59: starts the hop's capture once however often it is told the same", () => {
        const capture = freshCapture();

        capture.setHeard(true);
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        capture.setStream(stream("microphone"));

        expect(captures.started).toHaveLength(1);
    });

    it("CALL-59: moves to a replaced stream by stopping the first capture", () => {
        const capture = freshCapture();
        capture.setHeard(true);
        capture.setStream(stream("first"));

        capture.setStream(stream("second"));

        expect(captures.started.map((started) => [started.options.stream, started.stopped])).toEqual([
            ["first", true],
            ["second", false],
        ]);
    });
});

describe("a hop that ended", () => {
    it("CALL-59: is opened again once when it ended past its deadline while both are still on", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await captures.started[0]?.end(PAST_DEADLINE);

        expect([live().length, captures.started.length]).toEqual([1, 2]);
    });

    it("CALL-59: is not opened a second time when the reopened hop ends past its deadline too", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await captures.started[0]?.end(PAST_DEADLINE);
        await captures.started[1]?.end(PAST_DEADLINE);

        expect([live().length, captures.started.length]).toEqual([0, 2]);
    });

    it("CALL-59: gets a new reopening after the person's next switch change", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        await captures.started[0]?.end(PAST_DEADLINE);
        await captures.started[1]?.end(PAST_DEADLINE);

        capture.setHeard(false);
        capture.setHeard(true);
        await captures.started[2]?.end(PAST_DEADLINE);

        expect([live().length, captures.started.length]).toEqual([1, 4]);
    });

    it("CALL-59: gets no new reopening from a notice that changes neither the switch nor the microphone", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        await captures.started[0]?.end(PAST_DEADLINE);

        capture.setHeard(true);
        capture.setStream(stream("microphone"));
        await captures.started[1]?.end(PAST_DEADLINE);

        expect([live().length, captures.started.length]).toEqual([0, 2]);
    });

    it.each([
        { kind: "hop", close: HopClose.BUSY },
        { kind: "hop", close: HopClose.SERVICE_FAILED },
        { kind: "hop", close: HopClose.NORMAL },
        { kind: "socket", code: HEARING_CLOSE.NO_TOKEN },
        { kind: "local", failure: "worklet-refused" },
    ] as const)("CALL-59: stays closed after %o until the switch or the microphone changes", async (end) => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);

        await captures.started[0]?.end(end);
        capture.setHeard(true);
        capture.setStream(stream("microphone"));
        expect(captures.started).toHaveLength(1);
        capture.setStream(stream("second"));

        expect(streamsOf()).toEqual(["second"]);
    });

    it("CALL-59: is not opened again when it ended past its deadline after the switch went off", async () => {
        const capture = freshCapture();
        capture.setStream(stream("microphone"));
        capture.setHeard(true);
        const first = captures.started[0];

        capture.setHeard(false);
        await first?.end(PAST_DEADLINE);

        expect(captures.started).toHaveLength(1);
    });

    it("CALL-59: is not mistaken for the running capture when an older one ends late", async () => {
        const capture = freshCapture();
        capture.setStream(stream("first"));
        capture.setHeard(true);
        const first = captures.started[0];
        capture.setStream(stream("second"));

        await first?.end(PAST_DEADLINE);

        expect([streamsOf(), captures.started.length]).toEqual([["second"], 2]);
    });
});
