// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { REMEMBERED_REQUESTS } from "./looking.ts";
import { ME, installedParticipation } from "./page.fixture.ts";
import {
    LOOK_ANSWER_SECONDS,
    LOOK_MAX_BYTES,
    LOOK_MAX_PIXELS,
    LOOK_PART_CHARACTERS,
    PARTICIPATION_SIGNAL,
} from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

const joined = (sponsorId: string, agentId = "a1") => ({
    agent_id: agentId,
    name: "Claude",
    sponsor: "Ada",
    sponsor_id: sponsorId,
});

type Page = Awaited<ReturnType<typeof installedParticipation>>;

const seeingOf = (page: Page, agentId = "a1") => page.tiles.get(`agent-${agentId}`)?.seeing;

async function pageWithAgent() {
    const page = await installedParticipation();
    page.seat(false);
    page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));
    return page;
}

// The answer to a request is sent one promise turn after the camera gave its picture.
const answered = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

afterEach(() => {
    vi.useRealTimers();
});

describe("the sponsor's seeing switch on an agent's tile", () => {
    it("CALL-62: shows no switch until the server's record arrives, which only the sponsor gets", async () => {
        const page = await pageWithAgent();

        expect(seeingOf(page)).toBeUndefined();
    });

    it("CALL-62: sends the new value on a press and shows what the server answers, never its own flip", async () => {
        const page = await pageWithAgent();
        page.emit(PARTICIPATION_SIGNAL.AGENT_SEEING, { agent_id: "a1", on: false });

        seeingOf(page)?.onChange(true);
        const beforeTheAnswer = seeingOf(page)?.on;
        page.emit(PARTICIPATION_SIGNAL.AGENT_SEEING, { agent_id: "a1", on: true });

        expect([page.sent, beforeTheAnswer, seeingOf(page)?.on]).toEqual([
            [{ type: PARTICIPATION_SIGNAL.AGENT_SEEING, agent_id: "a1", on: true }],
            false,
            true,
        ]);
    });

    it("CALL-62: leaves the hearing switch as it is", async () => {
        const page = await pageWithAgent();
        page.emit(PARTICIPATION_SIGNAL.AGENT_HEARING, { agent_id: "a1", on: true });

        page.emit(PARTICIPATION_SIGNAL.AGENT_SEEING, { agent_id: "a1", on: false });

        expect([page.tiles.get("agent-a1")?.hearing?.on, seeingOf(page)?.on]).toEqual([true, false]);
    });

    it("CALL-62: keeps the switch when the agent's tile is announced again", async () => {
        const page = await pageWithAgent();
        page.emit(PARTICIPATION_SIGNAL.AGENT_SEEING, { agent_id: "a1", on: true });

        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined(ME));

        expect(seeingOf(page)?.on).toBe(true);
    });

    it("CALL-62: ignores a record for an agent it does not show", async () => {
        const page = await pageWithAgent();

        page.emit(PARTICIPATION_SIGNAL.AGENT_SEEING, { agent_id: "another", on: true });

        expect([...page.tiles.keys()]).toEqual(["agent-a1"]);
    });
});

describe("a look at the person's camera", () => {
    it("CALL-63: answers a request with the picture in parts of the frame's size, within the bounds it was given", async () => {
        const page = await pageWithAgent();
        const picture = "A".repeat(LOOK_PART_CHARACTERS * 2 + 12);
        page.cameraGives(picture);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: "r1" });
        await answered();

        expect(page.pictureBounds).toEqual([
            { maxPixels: LOOK_MAX_PIXELS, maxBytes: LOOK_MAX_BYTES, maxMilliseconds: LOOK_ANSWER_SECONDS * 1000 },
        ]);
        expect(page.sent).toEqual([]);
        expect(page.sentTransient).toEqual(
            [0, 1, 2].map((index) => ({
                type: PARTICIPATION_SIGNAL.AGENT_LOOK_PART,
                request_id: "r1",
                index,
                count: 3,
                data: picture.slice(index * LOOK_PART_CHARACTERS, (index + 1) * LOOK_PART_CHARACTERS),
            })),
        );
    });

    it.each([
        ["gives no picture", null],
        ["fails", new Error("the camera is gone")],
        ["gives an empty picture", ""],
    ])("CALL-63: answers that there is no camera when the camera %s", async (_case, gives) => {
        const page = await pageWithAgent();
        page.cameraGives(gives);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: "r1" });
        await answered();

        expect(page.sent).toEqual([{ type: PARTICIPATION_SIGNAL.AGENT_LOOK_NONE, request_id: "r1" }]);
    });

    it.each([
        ["a picture", "AAAA"],
        ["no picture", null],
    ])("CALL-63: drops the answer of %s that the camera gives after the call ended", async (_case, gives) => {
        const page = await pageWithAgent();
        const give = page.cameraGivesLater();
        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: "r1" });

        page.endCall();
        give(gives);
        await answered();

        expect([...page.sent, ...page.sentTransient]).toEqual([]);
    });

    it("CALL-63: answers a request once, though the server repeats it", async () => {
        const page = await pageWithAgent();
        page.cameraGives("AAAA");

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: "r1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: "r1" });
        await answered();

        expect(page.sentTransient).toHaveLength(1);
    });

    it("CALL-63: remembers only the newest requests it answered, so the oldest one is answered again", async () => {
        const page = await pageWithAgent();
        page.cameraGives("AAAA");
        const request = (index: number) => {
            page.emit(PARTICIPATION_SIGNAL.AGENT_LOOK_REQUEST, { request_id: `r${String(index)}` });
        };
        for (let index = 0; index <= REMEMBERED_REQUESTS; index += 1) request(index);
        await answered();
        const afterTheRequests = page.sentTransient.length;

        request(REMEMBERED_REQUESTS);
        await answered();
        const afterTheNewest = page.sentTransient.length;
        request(0);
        await answered();

        expect([afterTheRequests, afterTheNewest, page.sentTransient.length]).toEqual([
            REMEMBERED_REQUESTS + 1,
            REMEMBERED_REQUESTS + 1,
            REMEMBERED_REQUESTS + 2,
        ]);
    });

    it("CALL-63: shows the mark on the agent's tile at a look and takes it away after a while", async () => {
        vi.useFakeTimers();
        const page = await pageWithAgent();

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });
        const during = page.tiles.get("agent-a1")?.looking;
        vi.advanceTimersByTime(3000);

        expect([during, page.tiles.get("agent-a1")?.looking]).toEqual([true, false]);
    });

    it("CALL-63: starts the mark's time over at a look in between", async () => {
        vi.useFakeTimers();
        const page = await pageWithAgent();
        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });
        vi.advanceTimersByTime(2000);

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });
        vi.advanceTimersByTime(2000);

        expect(page.tiles.get("agent-a1")?.looking).toBe(true);
    });

    it("CALL-63: shows the mark on every seat's page, not only the sponsor's", async () => {
        vi.useFakeTimers();
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });

        expect(page.tiles.get("agent-a1")?.looking).toBe(true);
    });

    it("CALL-63: shows no mark for an agent it does not show, and none after the agent left", async () => {
        vi.useFakeTimers();
        const page = await pageWithAgent();
        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });

        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_LOOKED, { agent_id: "a1" });
        vi.advanceTimersByTime(3000);

        expect([...page.tiles.keys()]).toEqual([]);
    });
});
