import { describe, expect, it } from "vitest";
import type { OutgoingFrame } from "#lib/call/feature-port.ts";
import { Table, type TableFrame } from "./table.svelte.ts";
import { SKAT_SIGNAL } from "./wire.ts";

const ME = "me";
const SOMEONE = "someone";

function stateOf(revision: number, attachment: number | null, openerId: string | null = SOMEONE): TableFrame {
    if (attachment === null) return { revision, attachment, src: null, opener_id: null };
    return { revision, attachment, src: `https://skat.example/t/${String(attachment)}?mode=call`, opener_id: openerId };
}

// A table on a page seated as `ME`; `frames` collects what the stage was told, in order, and `sent` what went out.
function seatedTable({ isHost = false } = {}) {
    const frames: string[] = [];
    const sent: OutgoingFrame[] = [];
    const table = new Table(
        {
            show: (src) => void frames.push(src),
            hide: () => void frames.push("hidden"),
        },
        (frame) => void sent.push(frame),
    );
    table.seated(ME, isHost);
    return { table, frames, sent };
}

describe("a table the server shows", () => {
    it("shows the frame of an attachment once and keeps it while only the opener changes", () => {
        const { table, frames } = seatedTable();

        table.receive(stateOf(1, 1));
        table.receive(stateOf(2, 1, null));
        table.receive(stateOf(2, 1, null));

        expect(frames).toEqual(["https://skat.example/t/1?mode=call"]);
    });

    it("takes the table away when the state has none, and only if one was shown", () => {
        const { table, frames } = seatedTable();

        table.receive(stateOf(1, null));
        table.receive(stateOf(2, 2));
        table.receive(stateOf(3, null));

        expect(frames).toEqual(["https://skat.example/t/2?mode=call", "hidden"]);
    });

    it("drops a state older than one it has seen", () => {
        const { table, frames } = seatedTable();

        table.receive(stateOf(5, null));
        table.receive(stateOf(3, 3));

        expect(frames).toEqual([]);
    });

    it("loads a new frame for another attachment", () => {
        const { table, frames } = seatedTable();

        table.receive(stateOf(1, 1));
        table.receive(stateOf(2, 2));

        expect(frames).toHaveLength(2);
    });

    it.each([
        ["an attach, the opener's leaving and a detach", [stateOf(1, 1), stateOf(2, 1, null), stateOf(3, null)]],
        ["the same states in the reverse order", [stateOf(3, null), stateOf(2, 1, null), stateOf(1, 1)]],
        ["the opener's leaving before the attach", [stateOf(2, 1, null), stateOf(1, 1), stateOf(3, null)]],
        ["a detach before the earlier states", [stateOf(3, null), stateOf(1, 1), stateOf(2, 1, null)]],
    ])("ends on the latest revision whatever the order of %s", (_name, states) => {
        const { table, frames } = seatedTable();

        for (const state of states) table.receive(state);

        expect(frames.at(-1) ?? "hidden").toBe("hidden");
    });

    it("ends on the table of the latest revision when frames arrive out of order", () => {
        const { table, frames } = seatedTable();

        table.receive(stateOf(3, 3));
        table.receive(stateOf(2, null));
        table.receive(stateOf(1, 1));

        expect(frames).toEqual(["https://skat.example/t/3?mode=call"]);
    });

    it("clears a stale table when a fresh join says there is none", () => {
        const { table, frames } = seatedTable();
        table.receive(stateOf(4, 4));

        table.receive(stateOf(9, null));

        expect(frames.at(-1)).toBe("hidden");
    });

    it("applies a state of the revision it already has, which a fresh join repeats", () => {
        const { table, frames } = seatedTable();
        table.receive(stateOf(4, null));

        table.receive(stateOf(4, 4));

        expect(frames).toHaveLength(1);
    });

    it("tells the stage nothing when the call ends with no table shown", () => {
        const { table, frames } = seatedTable();

        table.reset();

        expect(frames).toEqual([]);
    });

    it("forgets its revisions when the call ends, so the next call starts afresh", () => {
        const { table, frames } = seatedTable();
        table.receive(stateOf(50, 1));

        table.reset();
        table.receive(stateOf(1, 2));

        expect(frames).toEqual(["https://skat.example/t/1?mode=call", "hidden", "https://skat.example/t/2?mode=call"]);
    });
});

describe("who may remove the table", () => {
    it.each([
        ["the opener", ME, false, true],
        ["the host", SOMEONE, true, true],
        ["anyone once the opener has left", null, false, true],
        ["a seat that is neither while the opener is there", SOMEONE, false, false],
    ])("%s", (_name, openerId, isHost, mayRemove) => {
        const { table } = seatedTable({ isHost });

        table.receive(stateOf(1, 1, openerId));

        expect(table.mayRemove).toBe(mayRemove);
    });

    it("follows the server's state after a fresh join gave the seat a new id", () => {
        const { table } = seatedTable();
        table.receive(stateOf(1, 1, ME));

        table.seated("a-new-seat", false);

        expect(table.mayRemove).toBe(false);
    });
});

describe("removing the table", () => {
    it("asks the server to detach the attachment it shows", () => {
        const { table, sent } = seatedTable();
        table.receive(stateOf(7, 7, ME));

        table.remove();

        expect(sent).toEqual([{ type: SKAT_SIGNAL.SKAT_DETACH, attachment: 7 }]);
    });

    it("asks for nothing while no table is shown", () => {
        const { table, sent } = seatedTable();

        table.remove();

        expect(sent).toEqual([]);
    });
});
