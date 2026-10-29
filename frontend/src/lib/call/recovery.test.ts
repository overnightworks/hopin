import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { ICE_DISCONNECTED_GRACE_MS, restartDelay, watchIceConnection } from "./recovery.ts";

const restartDelayByAttempt: [number, number][] = [
    [0, 2000],
    [1, 4000],
    [2, 8000],
    [3, 15000],
    [4, 15000],
    [5, 15000],
];

it.each(restartDelayByAttempt)("restart delay of attempt %i is %i ms and the last delay repeats", (attempt, delay) => {
    expect(restartDelay(attempt)).toBe(delay);
});

class FakeConnection extends EventTarget {
    iceConnectionState: RTCIceConnectionState = "connected";
    signalingState: RTCSignalingState = "stable";

    changeTo(state: RTCIceConnectionState): void {
        this.iceConnectionState = state;
        this.dispatchEvent(new Event("iceconnectionstatechange"));
    }
}

describe("watching an ICE connection", () => {
    let connection: FakeConnection;
    let restart: Mock<() => void>;

    beforeEach(() => {
        vi.useFakeTimers();
        connection = new FakeConnection();
        restart = vi.fn<() => void>();
        watchIceConnection(connection, restart);
    });

    afterEach(() => {
        vi.useRealTimers();
    });

    it("waits out the grace of a disconnect before restarting", () => {
        connection.changeTo("disconnected");

        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS - 1);
        expect(restart).not.toHaveBeenCalled();

        vi.advanceTimersByTime(1);
        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("does not restart when the connection recovers within the grace", () => {
        connection.changeTo("disconnected");
        connection.changeTo("checking");

        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS);

        expect(restart).not.toHaveBeenCalled();
    });

    it("does not restart when the connection is connected again within the grace", () => {
        connection.changeTo("disconnected");
        connection.changeTo("connected");

        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS);

        expect(restart).not.toHaveBeenCalled();
    });

    it("keeps the first grace when a second disconnect arrives during it", () => {
        connection.changeTo("disconnected");
        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS - 1);
        connection.changeTo("disconnected");

        vi.advanceTimersByTime(1);

        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("restarts at once when the connection fails", () => {
        connection.changeTo("failed");

        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("ignores states that are neither lost nor connected", () => {
        connection.changeTo("checking");
        connection.changeTo("new");

        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS * 10);

        expect(restart).not.toHaveBeenCalled();
    });

    it("retries after the growing delays until the connection is connected again", () => {
        connection.changeTo("failed");
        expect(restart).toHaveBeenCalledTimes(1);

        vi.advanceTimersByTime(1999);
        expect(restart).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(1);
        expect(restart).toHaveBeenCalledTimes(2);

        vi.advanceTimersByTime(4000);
        expect(restart).toHaveBeenCalledTimes(3);
        vi.advanceTimersByTime(8000);
        expect(restart).toHaveBeenCalledTimes(4);
        vi.advanceTimersByTime(15000);
        expect(restart).toHaveBeenCalledTimes(5);
        vi.advanceTimersByTime(15000);
        expect(restart).toHaveBeenCalledTimes(6);

        connection.changeTo("connected");
        vi.advanceTimersByTime(60000);
        expect(restart).toHaveBeenCalledTimes(6);
    });

    it("ignores further state changes while an episode runs", () => {
        connection.changeTo("failed");
        connection.changeTo("disconnected");
        connection.changeTo("failed");

        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("starts the delays over after a connected state ends an episode", () => {
        connection.changeTo("failed");
        vi.advanceTimersByTime(2000);
        connection.changeTo("completed");
        restart.mockClear();

        connection.changeTo("failed");
        expect(restart).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(1999);
        expect(restart).toHaveBeenCalledTimes(1);
        vi.advanceTimersByTime(1);
        expect(restart).toHaveBeenCalledTimes(2);
    });

    it("does not retry once a retry finds the connection connected", () => {
        connection.changeTo("failed");
        connection.iceConnectionState = "connected";

        vi.advanceTimersByTime(60000);

        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("waits for a stable signaling state before the first restart", () => {
        connection.signalingState = "have-local-offer";
        connection.changeTo("failed");
        expect(restart).not.toHaveBeenCalled();

        vi.advanceTimersByTime(1999);
        expect(restart).not.toHaveBeenCalled();

        connection.signalingState = "stable";
        vi.advanceTimersByTime(1);
        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("does nothing when the connection closed before a timer fires", () => {
        connection.changeTo("failed");
        connection.signalingState = "closed";

        vi.advanceTimersByTime(60000);

        expect(restart).toHaveBeenCalledTimes(1);
    });

    it("does not restart after the grace when the connection closed meanwhile", () => {
        connection.changeTo("disconnected");
        connection.signalingState = "closed";

        vi.advanceTimersByTime(ICE_DISCONNECTED_GRACE_MS);

        expect(restart).not.toHaveBeenCalled();
    });
});
