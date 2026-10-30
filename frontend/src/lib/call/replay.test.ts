import { describe, expect, it } from "vitest";
import { REPLAY_LOG_LIMIT, SIGNAL_TYPE } from "#lib/wire.ts";
import { SentLog } from "#lib/call/replay.ts";

const { ANSWER, CHAT, ICE_CANDIDATE, OFFER } = SIGNAL_TYPE;

const keptAndFloorByFrameTypes: [string, SIGNAL_TYPE[], number[], number][] = [
    ["chat frames are all kept", [CHAT, CHAT], [1, 2], 0],
    ["an offer supersedes the earlier offer and candidate", [OFFER, ICE_CANDIDATE, OFFER], [3], 0],
    [
        "an answer supersedes the earlier answer and candidate",
        [ANSWER, ICE_CANDIDATE, ANSWER, ICE_CANDIDATE],
        [3, 4],
        0,
    ],
    [
        "the log keeps the server's limit and raises the floor past what it drops",
        new Array<SIGNAL_TYPE>(REPLAY_LOG_LIMIT + 3).fill(CHAT),
        Array.from({ length: REPLAY_LOG_LIMIT }, (_, index) => index + 4),
        3,
    ],
];

describe("SentLog", () => {
    it.each(keptAndFloorByFrameTypes)("%s", (_name, frameTypes, kept, floor) => {
        const log = new SentLog();

        const seqs = frameTypes.map((type) => log.append({ type, target: "peer" }).seq);

        expect(seqs).toEqual(frameTypes.map((_type, index) => index + 1));
        expect(log.unacknowledged().map((frame) => frame.seq)).toEqual(kept);
        expect(log.floor).toBe(floor);
    });
});
