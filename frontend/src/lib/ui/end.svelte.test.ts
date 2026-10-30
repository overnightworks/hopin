// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { ERROR_REASON, ROOM_CLOSED_REASON } from "#lib/wire.ts";
import {
    END_ACTION,
    END_ICON,
    END_KIND,
    endKindOfClosedRoom,
    endKindOfRefusal,
    rejoinCall,
    actionClasses,
    shownEndView,
    showEnd,
} from "#lib/ui/end.svelte.ts";
import { SCREEN, screens } from "#lib/ui/screens.svelte.ts";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

afterEach(() => {
    vi.unstubAllGlobals();
    history.pushState(null, "", "/");
});

const STILL_TALKING = "The others are still talking.";

describe("showEnd", () => {
    it.each([
        {
            kind: END_KIND.HOST_GONE,
            frame: { grace_seconds: 30 },
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: "Call ended",
                text: "The host didn't come back within 30 seconds, so the call ended for everyone.",
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.ENDED_BY_HOST,
            frame: {},
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: "Call ended",
                text: "The host ended the call for everyone.",
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.LEFT,
            frame: {},
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: "You left the call",
                text: STILL_TALKING,
                pill: null,
                actions: [END_ACTION.HOME, END_ACTION.REJOIN],
            },
        },
        {
            kind: END_KIND.HOST_LEFT,
            frame: {},
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: "You left the call",
                text: STILL_TALKING,
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.EXPIRED,
            frame: {},
            expected: {
                icon: END_ICON.HOURGLASS,
                isWarning: false,
                title: "This link has expired",
                text: "The call is over. Ask for a new link.",
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.LOCKED,
            frame: {},
            expected: {
                icon: END_ICON.LOCK,
                isWarning: true,
                title: "Call is locked",
                text: "The host isn't letting anyone new in right now. Try again in a moment.",
                pill: null,
                actions: [END_ACTION.RETRY, END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.FULL,
            frame: { capacity: 8 },
            expected: {
                icon: END_ICON.USERS,
                isWarning: true,
                title: "Call is full",
                text: "A call has room for 8 people.",
                pill: "8/8",
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.TOO_MANY_CALLS,
            frame: { limit: 3 },
            expected: {
                icon: END_ICON.USERS,
                isWarning: true,
                title: "Too many calls open",
                text: "Your account is in 3 calls. Leave one to join this one.",
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
    ])("shows the $kind end", ({ kind, frame, expected }) => {
        showEnd(kind, frame);

        expect(shownEndView()).toEqual(expected);
        expect(screens.current).toBe(SCREEN.END);
    });

    it("shows a kind given without a frame", () => {
        showEnd(END_KIND.LEFT);

        expect(shownEndView().title).toBe("You left the call");
    });

    it("replaces the pill of an earlier end", () => {
        showEnd(END_KIND.FULL, { capacity: 4 });
        showEnd(END_KIND.LEFT);

        expect(shownEndView().pill).toBeNull();
    });
});

describe("shownEndView", () => {
    it("shows an empty ended view before any end is shown", async () => {
        vi.resetModules();
        const fresh = await import("#lib/ui/end.svelte.ts");

        expect(fresh.shownEndView()).toEqual({
            icon: null,
            isWarning: false,
            title: "Call ended",
            text: "",
            pill: null,
            actions: [],
        });
    });
});

describe("end kinds", () => {
    it.each([
        [ERROR_REASON.NOT_FOUND, END_KIND.EXPIRED],
        [ERROR_REASON.LOCKED, END_KIND.LOCKED],
        [ERROR_REASON.FULL, END_KIND.FULL],
        [ERROR_REASON.TOO_MANY_CALLS, END_KIND.TOO_MANY_CALLS],
        [ERROR_REASON.NOT_HOST, null],
    ])("maps the refusal %s to %s", (reason, kind) => {
        expect(endKindOfRefusal(reason)).toBe(kind);
    });

    it.each([
        [ROOM_CLOSED_REASON.HOST_GONE, END_KIND.HOST_GONE],
        [ROOM_CLOSED_REASON.ENDED, END_KIND.ENDED_BY_HOST],
    ])("maps the closed room reason %s to %s", (reason, kind) => {
        expect(endKindOfClosedRoom(reason)).toBe(kind);
    });
});

describe("rejoinCall", () => {
    it("opens the call of the address at the time of the click", () => {
        const assign = vi.fn();
        vi.stubGlobal("location", { pathname: "/c/abc", origin: "https://hopin.test", assign });

        rejoinCall();

        expect(assign).toHaveBeenCalledWith("https://hopin.test/c/abc");
    });

    it("does nothing when the address names no call", () => {
        const assign = vi.fn();
        vi.stubGlobal("location", { pathname: "/", origin: "https://hopin.test", assign });

        rejoinCall();

        expect(assign).not.toHaveBeenCalled();
    });
});

describe("actionClasses", () => {
    it.each([
        [END_KIND.LOCKED, END_ACTION.RETRY, ["button", "button-block", false, "button-primary", "button-lg"]],
        [END_KIND.LOCKED, END_ACTION.HOME, ["button", "button-block", false, "button-text"]],
        [END_KIND.LOCKED, END_ACTION.REJOIN, ["button", "button-block", "hidden", "button-text"]],
        [END_KIND.LEFT, END_ACTION.REJOIN, ["button", "button-block", false, "button-text"]],
        [END_KIND.HOST_GONE, END_ACTION.RETRY, ["button", "button-block", "hidden", "button-text"]],
    ])("styles the %s end's %s button", (kind, action, expected) => {
        showEnd(kind);

        expect(actionClasses(action)).toEqual(expected);
    });
});
