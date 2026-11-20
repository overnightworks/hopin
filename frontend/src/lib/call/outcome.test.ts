import { describe, expect, it } from "vitest";
import { CONNECTION_OUTCOME } from "#lib/wire.ts";
import { reportConnectionOutcomes } from "./outcome.ts";

type Report = Record<string, unknown>;

class FakeConnection extends EventTarget {
    connectionState: RTCPeerConnectionState = "new";
    reports: Report[];

    constructor(selectedCandidateTypes: [string, string]) {
        super();
        this.reports = [
            { id: "transport", type: "transport", selectedCandidatePairId: "pair" },
            { id: "other", type: "candidate-pair", localCandidateId: "far", remoteCandidateId: "far" },
            { id: "pair", type: "candidate-pair", localCandidateId: "local", remoteCandidateId: "remote" },
            { id: "local", type: "local-candidate", candidateType: selectedCandidateTypes[0] },
            { id: "remote", type: "remote-candidate", candidateType: selectedCandidateTypes[1] },
            { id: "far", type: "local-candidate", candidateType: "relay" },
        ];
    }

    getStats = () => Promise.resolve(new Map(this.reports.map((report) => [report.id as string, report])));

    changeTo(state: RTCPeerConnectionState): void {
        this.connectionState = state;
        this.dispatchEvent(new Event("connectionstatechange"));
    }
}

function watched(selectedCandidateTypes: [string, string] = ["host", "host"]) {
    const connection = new FakeConnection(selectedCandidateTypes);
    const outcomes: CONNECTION_OUTCOME[] = [];
    reportConnectionOutcomes(connection, (outcome) => outcomes.push(outcome));
    return { connection, outcomes };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

describe("reporting how a peer connection went", () => {
    it.each([
        [["host", "srflx"], CONNECTION_OUTCOME.DIRECT],
        [["relay", "host"], CONNECTION_OUTCOME.RELAYED],
        [["host", "relay"], CONNECTION_OUTCOME.RELAYED],
    ] as [[string, string], CONNECTION_OUTCOME][])(
        "CALL-76: a connection coming up over %j is %s, judged by the selected pair only",
        async (types, outcome) => {
            const { connection, outcomes } = watched(types);
            connection.changeTo("connected");
            await settle();
            expect(outcomes).toEqual([outcome]);
        },
    );

    it("CALL-76: a drop that fails and then comes back is failed once and then resumed", async () => {
        const { connection, outcomes } = watched();
        connection.changeTo("connected");
        await settle();
        connection.changeTo("disconnected");
        connection.changeTo("failed");
        connection.changeTo("failed");
        connection.changeTo("connected");
        await settle();
        expect(outcomes).toEqual([CONNECTION_OUTCOME.DIRECT, CONNECTION_OUTCOME.FAILED, CONNECTION_OUTCOME.RESUMED]);
    });

    it("CALL-76: a short drop that heals before it fails is resumed and not failed", async () => {
        const { connection, outcomes } = watched();
        connection.changeTo("connected");
        await settle();
        connection.changeTo("disconnected");
        connection.changeTo("connected");
        await settle();
        expect(outcomes).toEqual([CONNECTION_OUTCOME.DIRECT, CONNECTION_OUTCOME.RESUMED]);
    });

    it("CALL-76: a connection that never came up is failed, and its later coming up is direct, not resumed", async () => {
        const { connection, outcomes } = watched();
        connection.changeTo("failed");
        connection.changeTo("connected");
        await settle();
        expect(outcomes).toEqual([CONNECTION_OUTCOME.FAILED, CONNECTION_OUTCOME.DIRECT]);
    });

    it("CALL-76: a state that is neither up nor lost reports nothing", async () => {
        const { connection, outcomes } = watched();
        connection.changeTo("connecting");
        connection.changeTo("closed");
        await settle();
        expect(outcomes).toEqual([]);
    });
});
