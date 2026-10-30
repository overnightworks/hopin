// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SIGNAL_TYPE } from "#lib/wire.ts";

const media = vi.hoisted(() => {
    class CameraError extends Error {
        constructor(cause: Error) {
            super(cause.message);
            this.name = cause.name;
        }
    }
    return {
        CameraError,
        addCamera: vi.fn(),
        releaseCamera: vi.fn(),
        toggleAudio: vi.fn(),
        toggleScreenShare: vi.fn(),
    };
});
const signaling = vi.hoisted(() => ({ isConnected: vi.fn(), send: vi.fn() }));
vi.mock("#lib/call/media.ts", () => media);
vi.mock("#lib/call/signaling.ts", () => signaling);

// The modules hold their state, so each case loads fresh copies.
async function freshControls() {
    vi.resetModules();
    const { state } = await import("#lib/call/state.ts");
    const { statusLine } = await import("#lib/ui/status.svelte.ts");
    const { toastState } = await import("#lib/ui/toast.svelte.ts");
    const controls = await import("#lib/ui/controls.svelte.ts");
    return { ...controls, state, statusLine, toastState };
}

const aStream = () => ({}) as MediaStream;

beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
});

afterEach(() => {
    vi.useRealTimers();
    document.body.innerHTML = "";
});

describe("device buttons view", () => {
    it("starts like the static markup: enabled and not sharing", async () => {
        const { deviceButtons } = await freshControls();

        expect(deviceButtons).toEqual({ hasMedia: true, isMicrophoneOn: true, isCameraOn: true, isSharing: false });
    });

    it("shows no media when the call has no local stream", async () => {
        const { deviceButtons, renderControls } = await freshControls();

        renderControls();

        expect(deviceButtons.hasMedia).toBe(false);
    });

    it("copies the microphone, camera and sharing state of the call", async () => {
        const { deviceButtons, renderControls, state } = await freshControls();
        state.localStream = aStream();
        state.audioEnabled = false;
        state.videoEnabled = false;
        state.screenStream = aStream();

        renderControls();

        expect(deviceButtons).toEqual({ hasMedia: true, isMicrophoneOn: false, isCameraOn: false, isSharing: true });
    });
});

describe("mute", () => {
    it("does nothing without a stream", async () => {
        const { muteClicked } = await freshControls();

        muteClicked();

        expect(media.toggleAudio).not.toHaveBeenCalled();
    });

    it("toggles the microphone and redraws with a stream", async () => {
        const { deviceButtons, muteClicked, state } = await freshControls();
        state.localStream = aStream();
        media.toggleAudio.mockImplementation(() => {
            state.audioEnabled = false;
        });

        muteClicked();

        expect(deviceButtons.isMicrophoneOn).toBe(false);
    });
});

describe("camera", () => {
    it("does nothing without a stream", async () => {
        const { cameraClicked } = await freshControls();

        await cameraClicked();

        expect(media.addCamera).not.toHaveBeenCalled();
        expect(media.releaseCamera).not.toHaveBeenCalled();
    });

    it("releases the camera when it is on and redraws", async () => {
        const { cameraClicked, deviceButtons, state } = await freshControls();
        state.localStream = aStream();
        media.releaseCamera.mockImplementation(() => {
            state.videoEnabled = false;
        });

        await cameraClicked();

        expect(deviceButtons.isCameraOn).toBe(false);
    });

    it("adds the camera when it is off and redraws", async () => {
        const { cameraClicked, deviceButtons, renderControls, state } = await freshControls();
        state.localStream = aStream();
        state.videoEnabled = false;
        renderControls();
        expect(deviceButtons.isCameraOn).toBe(false);
        media.addCamera.mockImplementation(() => {
            state.videoEnabled = true;
            return Promise.resolve();
        });

        await cameraClicked();

        expect(deviceButtons.isCameraOn).toBe(true);
    });

    it("says no camera was found, then returns to in call while a room is set", async () => {
        const { cameraClicked, state, statusLine } = await freshControls();
        state.localStream = aStream();
        state.videoEnabled = false;
        state.roomId = "room";
        media.addCamera.mockRejectedValue(Object.assign(new Error("denied"), { name: "NotAllowedError" }));

        await cameraClicked();
        expect(statusLine).toEqual({ text: "No camera found (NotAllowedError)", type: "error" });

        vi.advanceTimersByTime(2000);
        expect(statusLine).toEqual({ text: "In call", type: "connected" });
    });

    it("keeps the camera error when the call ended before the reset", async () => {
        const { cameraClicked, state, statusLine } = await freshControls();
        state.localStream = aStream();
        state.videoEnabled = false;
        media.addCamera.mockRejectedValue(new Error("denied"));

        await cameraClicked();
        vi.advanceTimersByTime(2000);

        expect(statusLine.type).toBe("error");
    });
});

describe("screen share", () => {
    it("does nothing without a stream", async () => {
        const { screenClicked } = await freshControls();

        await screenClicked();

        expect(media.toggleScreenShare).not.toHaveBeenCalled();
    });

    it("redraws as sharing when the share starts", async () => {
        const { deviceButtons, screenClicked, state } = await freshControls();
        state.localStream = aStream();
        media.toggleScreenShare.mockImplementation(() => {
            state.screenStream = aStream();
            return Promise.resolve();
        });

        await screenClicked();

        expect(deviceButtons.isSharing).toBe(true);
    });

    it("reports a camera error like the camera button does", async () => {
        const { screenClicked, state, statusLine } = await freshControls();
        state.localStream = aStream();
        media.toggleScreenShare.mockRejectedValue(
            new media.CameraError(Object.assign(new Error("x"), { name: "NotFoundError" })),
        );

        await screenClicked();

        expect(statusLine.text).toBe("No camera found (NotFoundError)");
    });

    it("reports any other failure with its message", async () => {
        const { screenClicked, state, statusLine } = await freshControls();
        state.localStream = aStream();
        media.toggleScreenShare.mockRejectedValue(new Error("picker closed"));

        await screenClicked();

        expect(statusLine).toEqual({ text: "Screen share failed: picker closed", type: "error" });
    });

    it("ends the share and redraws when the browser ends it", async () => {
        const { deviceButtons, screenClicked, state } = await freshControls();
        state.localStream = aStream();
        let browserEnded = () => {};
        media.toggleScreenShare.mockImplementationOnce((onEnded: () => void) => {
            state.screenStream = aStream();
            browserEnded = onEnded;
            return Promise.resolve();
        });
        await screenClicked();
        media.toggleScreenShare.mockImplementationOnce(() => {
            state.screenStream = null;
        });

        browserEnded();
        await vi.waitFor(() => {
            expect(deviceButtons.isSharing).toBe(false);
        });
    });
});

describe("lock click", () => {
    it("sends nothing while not connected", async () => {
        const { lockClicked, state } = await freshControls();
        state.isHost = true;
        signaling.isConnected.mockReturnValue(false);

        lockClicked();

        expect(signaling.send).not.toHaveBeenCalled();
    });

    it("sends nothing for a guest", async () => {
        const { lockClicked } = await freshControls();
        signaling.isConnected.mockReturnValue(true);

        lockClicked();

        expect(signaling.send).not.toHaveBeenCalled();
    });

    it.each([
        { isLocked: false, type: SIGNAL_TYPE.LOCK_ROOM },
        { isLocked: true, type: SIGNAL_TYPE.UNLOCK_ROOM },
    ])("sends $type to the host while the call is locked: $isLocked", async ({ isLocked, type }) => {
        const { lockClicked, state } = await freshControls();
        state.isHost = true;
        state.isLocked = isLocked;
        signaling.isConnected.mockReturnValue(true);

        lockClicked();

        expect(signaling.send).toHaveBeenCalledWith({ type });
    });
});

describe("lock switch view", () => {
    it("starts hidden, unchecked and without a line", async () => {
        const { lockSwitch } = await freshControls();

        expect(lockSwitch).toEqual({ isShown: false, isLocked: false, line: "" });
    });

    it.each([
        { isLocked: true, line: "No one new can join" },
        { isLocked: false, line: "New people can join" },
    ])("shows the switch with the line $line", async ({ isLocked, line }) => {
        const { lockSwitch, showLockButton } = await freshControls();

        showLockButton(isLocked);

        expect(lockSwitch).toEqual({ isShown: true, isLocked, line });
    });

    it("hides the switch but keeps its state and line", async () => {
        const { hideLockButton, lockSwitch, showLockButton } = await freshControls();
        showLockButton(true);

        hideLockButton();

        expect(lockSwitch).toEqual({ isShown: false, isLocked: true, line: "No one new can join" });
    });
});

describe("lock change toast", () => {
    it.each([
        { isLocked: true, text: "Call locked" },
        { isLocked: false, text: "Call unlocked" },
    ])("tells the host $text", async ({ isLocked, text }) => {
        const { confirmLockChange, state, toastState } = await freshControls();
        state.isHost = true;

        confirmLockChange(isLocked);

        expect(toastState.current?.text).toBe(text);
    });

    it("tells a guest nothing", async () => {
        const { confirmLockChange, toastState } = await freshControls();

        confirmLockChange(true);

        expect(toastState.current).toBeNull();
    });
});

describe("addMoreItems", () => {
    it("places the items before the separator, after Lock call", async () => {
        document.body.innerHTML = `<dialog id="moreMenu"><button id="lockBtn"></button><div class="sep"></div><a id="legal"></a></dialog>`;
        const { addMoreItems } = await freshControls();
        const first = document.createElement("button");
        const second = document.createElement("button");

        addMoreItems(first, second);

        const order = [...(document.querySelector("#moreMenu")?.children ?? [])].map(
            (child) => child.id || child.className,
        );
        expect(order).toEqual(["lockBtn", "", "", "sep", "legal"]);
    });
});

describe("addMoreItems without More", () => {
    it("fails loud when More has no separator", async () => {
        const { addMoreItems } = await freshControls();

        expect(() => {
            addMoreItems(document.createElement("button"));
        }).toThrow("no separator");
    });
});

describe("leave request", () => {
    it("reaches the installed callback", async () => {
        const { installLeaveRequested, requestLeave } = await freshControls();
        const callback = vi.fn();
        installLeaveRequested(callback);

        requestLeave();

        expect(callback).toHaveBeenCalledOnce();
    });

    it("fails loud when no callback is installed", async () => {
        const { requestLeave } = await freshControls();

        expect(() => {
            requestLeave();
        }).toThrow("not installed");
    });

    it("refuses a second callback", async () => {
        const { installLeaveRequested } = await freshControls();
        installLeaveRequested(() => {});

        expect(() => {
            installLeaveRequested(() => {});
        }).toThrow("already installed");
    });
});
