// How each peer connection went, reported to the server as a closed code and nothing else.

import { CONNECTION_OUTCOME } from "#lib/wire.ts";

export type OutcomeConnection = Pick<RTCPeerConnection, "addEventListener" | "connectionState" | "getStats">;

interface StatsReport {
    type: string;
    id: string;
    selectedCandidatePairId?: string;
    nominated?: boolean;
    state?: string;
    localCandidateId?: string;
    remoteCandidateId?: string;
    candidateType?: string;
}

/** The selected candidate pair runs through a TURN relay when either end of it is a relayed candidate. */
export async function outcomeOfEstablished(connection: OutcomeConnection): Promise<CONNECTION_OUTCOME> {
    const reports = [...(await connection.getStats()).values()] as StatsReport[];
    const selectedId = reports.find((report) => report.type === "transport")?.selectedCandidatePairId;
    const pair = reports.find(
        (report) =>
            report.type === "candidate-pair" &&
            (selectedId === undefined
                ? report.nominated === true && report.state === "succeeded"
                : report.id === selectedId),
    );
    const ends = reports.filter(
        (report) => report.id === pair?.localCandidateId || report.id === pair?.remoteCandidateId,
    );
    return ends.some((end) => end.candidateType === "relay") ? CONNECTION_OUTCOME.RELAYED : CONNECTION_OUTCOME.DIRECT;
}

/**
 * Reports a connection's first coming up as direct or relayed, its loss as failed once per drop, and its coming back
 * after a drop as resumed. A drop that heals before the connection fails is a resume too.
 */
export function reportConnectionOutcomes(
    connection: OutcomeConnection,
    report: (outcome: CONNECTION_OUTCOME) => void,
): void {
    let established = false;
    let lost = false;
    let failedReported = false;

    connection.addEventListener("connectionstatechange", () => {
        const state = connection.connectionState;
        if (state === "connected") {
            failedReported = false;
            if (lost) {
                lost = false;
                report(CONNECTION_OUTCOME.RESUMED);
            } else if (!established) {
                established = true;
                void outcomeOfEstablished(connection).then(report);
            }
        } else if (state === "disconnected") {
            lost = established;
        } else if (state === "failed") {
            lost = established;
            if (!failedReported) {
                failedReported = true;
                report(CONNECTION_OUTCOME.FAILED);
            }
        }
    });
}
