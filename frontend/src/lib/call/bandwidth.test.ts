import { describe, expect, it, vi } from "vitest";
import { applyUploadCaps, videoBitrateCap, videoTransceivers, type CappedConnection } from "./bandwidth.ts";

const videoCapByRemoteCount: [number, number][] = [
    [1, 2_000_000],
    [2, 1_000_000],
    [3, 666_666],
    [4, 500_000],
    [5, 400_000],
];

it.each(videoCapByRemoteCount)("video bitrate cap with %i remote peers is %i bps", (remoteCount, cap) => {
    expect(videoBitrateCap(remoteCount)).toBe(cap);
});

type SetParameters = (parameters: RTCRtpSendParameters) => Promise<void>;

interface FakeSender {
    readonly rtpSender: RTCRtpSender;
    readonly getParameters: ReturnType<typeof vi.fn>;
    readonly setParameters: ReturnType<typeof vi.fn<SetParameters>>;
    readonly encoding: { maxBitrate?: number };
}

function senderRejecting(...failures: Error[]): FakeSender {
    const encoding: { maxBitrate?: number } = {};
    const getParameters = vi.fn(() => ({ encodings: [encoding] }));
    const setParameters = vi.fn<SetParameters>();
    for (const failure of failures) setParameters.mockRejectedValueOnce(failure);
    setParameters.mockResolvedValue(undefined);
    const rtpSender = { getParameters, setParameters } as unknown as RTCRtpSender;
    return { rtpSender, getParameters, setParameters, encoding };
}

function transceiver(kind: "audio" | "video", sender: RTCRtpSender): RTCRtpTransceiver {
    return { receiver: { track: { kind } }, sender } as unknown as RTCRtpTransceiver;
}

function connectionOf(
    transceivers: RTCRtpTransceiver[],
    signalingState: RTCSignalingState = "stable",
): CappedConnection {
    return { getTransceivers: () => transceivers, signalingState };
}

const invalidState = () => new DOMException("getParameters() has never been called", "InvalidStateError");

describe("video transceivers", () => {
    it("are the transceivers that receive video", () => {
        const video = transceiver("video", senderRejecting().rtpSender);
        const audio = transceiver("audio", senderRejecting().rtpSender);

        expect(videoTransceivers(connectionOf([audio, video]))).toEqual([video]);
    });
});

describe("applying upload caps", () => {
    it("caps every video sender at the budget split by the connection count", async () => {
        const first = senderRejecting();
        const second = senderRejecting();
        const audio = senderRejecting();

        await applyUploadCaps([
            connectionOf([transceiver("video", first.rtpSender), transceiver("audio", audio.rtpSender)]),
            connectionOf([transceiver("video", second.rtpSender)]),
        ]);

        expect(first.encoding.maxBitrate).toBe(1_000_000);
        expect(second.encoding.maxBitrate).toBe(1_000_000);
        expect(audio.setParameters).not.toHaveBeenCalled();
    });

    it("caps nothing for no connection", async () => {
        await expect(applyUploadCaps([])).resolves.toBeUndefined();
    });

    it("leaves a closed connection alone", async () => {
        const sender = senderRejecting();

        await applyUploadCaps([connectionOf([transceiver("video", sender.rtpSender)], "closed")]);

        expect(sender.getParameters).not.toHaveBeenCalled();
        expect(sender.setParameters).not.toHaveBeenCalled();
    });

    it.each([1, 2])("reads and sets again after %i InvalidStateError rejections", async (rejections) => {
        const sender = senderRejecting(...Array.from({ length: rejections }, invalidState));

        await applyUploadCaps([connectionOf([transceiver("video", sender.rtpSender)])]);

        expect(sender.setParameters).toHaveBeenCalledTimes(rejections + 1);
        expect(sender.getParameters).toHaveBeenCalledTimes(rejections + 1);
        expect(sender.encoding.maxBitrate).toBe(2_000_000);
    });

    it("throws the InvalidStateError of the third attempt", async () => {
        const sender = senderRejecting(invalidState(), invalidState(), invalidState());

        await expect(applyUploadCaps([connectionOf([transceiver("video", sender.rtpSender)])])).rejects.toThrow(
            "getParameters() has never been called",
        );
        expect(sender.setParameters).toHaveBeenCalledTimes(3);
    });

    it("throws any other error at once", async () => {
        const sender = senderRejecting(new TypeError("not allowed"));

        await expect(applyUploadCaps([connectionOf([transceiver("video", sender.rtpSender)])])).rejects.toThrow(
            "not allowed",
        );
        expect(sender.setParameters).toHaveBeenCalledTimes(1);
    });

    it("lets a later cap on the same sender run after an earlier cap failed", async () => {
        const sender = senderRejecting(new TypeError("not allowed"));
        const connection = connectionOf([transceiver("video", sender.rtpSender)]);

        await expect(applyUploadCaps([connection])).rejects.toThrow("not allowed");
        await applyUploadCaps([connection, connection]);

        expect(sender.encoding.maxBitrate).toBe(1_000_000);
    });

    it("runs caps on one sender one after another, the latest last", async () => {
        const sender = senderRejecting();
        const connection = connectionOf([transceiver("video", sender.rtpSender)]);

        await Promise.all([applyUploadCaps([connection]), applyUploadCaps([connection, connection])]);

        expect(sender.setParameters).toHaveBeenCalledTimes(3);
        expect(sender.encoding.maxBitrate).toBe(1_000_000);
    });
});
