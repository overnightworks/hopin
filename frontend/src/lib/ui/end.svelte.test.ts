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
import { words } from "#lib/words/de.ts";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

afterEach(() => {
    vi.unstubAllGlobals();
    history.pushState(null, "", "/");
});

describe("showEnd", () => {
    it.each([
        {
            kind: END_KIND.HOST_GONE,
            frame: { grace_seconds: 30, host_name: "Ada" },
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: words["end.callEndedTitle"],
                text: words["end.hostGoneNamedText"]({ name: "Ada", seconds: "30" }),
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.ENDED_BY_HOST,
            frame: { host_name: "Ada" },
            expected: {
                icon: END_ICON.ENDED,
                isWarning: false,
                title: words["end.callEndedTitle"],
                text: words["end.endedByHostNamedText"]({ name: "Ada" }),
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
                title: words["end.leftTitle"],
                text: words["end.leftText"],
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
                title: words["end.leftTitle"],
                text: words["end.leftText"],
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
                title: words["end.expiredTitle"],
                text: words["end.expiredText"],
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
                title: words["end.lockedTitle"],
                text: words["end.lockedText"],
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
                title: words["end.fullTitle"],
                text: words["end.fullText"]({ capacity: "8" }),
                pill: words["end.fullPill"]({ capacity: "8" }),
                actions: [END_ACTION.HOME],
            },
        },
        {
            kind: END_KIND.TOO_MANY_CALLS,
            frame: { limit: 3 },
            expected: {
                icon: END_ICON.USERS,
                isWarning: true,
                title: words["end.tooManyCallsTitle"],
                text: words["end.tooManyCallsText"]({ limit: "3" }),
                pill: null,
                actions: [END_ACTION.HOME],
            },
        },
    ])("CALL-46: shows the $kind end", ({ kind, frame, expected }) => {
        showEnd(kind, frame);

        expect(shownEndView()).toEqual(expected);
        expect(screens.current).toBe(SCREEN.END);
    });

    it.each([
        [END_KIND.ENDED_BY_HOST, { host_name: "" }, words["end.endedByHostUnnamedText"]],
        [END_KIND.HOST_GONE, { host_name: "", grace_seconds: 30 }, words["end.hostGoneUnnamedText"]({ seconds: "30" })],
    ])("CALL-46: the %s end says the host without a name when the host's name is empty", (kind, frame, text) => {
        showEnd(kind, frame);

        expect(shownEndView().text).toBe(text);
    });

    it("shows a kind given without a frame", () => {
        showEnd(END_KIND.LEFT);

        expect(shownEndView().title).toBe(words["end.leftTitle"]);
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
            title: words["end.callEndedTitle"],
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
        [END_KIND.LOCKED, END_ACTION.RETRY, ["button", "button-block", "button-primary", "button-lg"]],
        [END_KIND.LOCKED, END_ACTION.HOME, ["button", "button-block", "button-text"]],
        [END_KIND.LOCKED, END_ACTION.REJOIN, ["button", "button-block", "hidden", "button-text"]],
        [END_KIND.LEFT, END_ACTION.REJOIN, ["button", "button-block", "button-text"]],
        [END_KIND.HOST_GONE, END_ACTION.RETRY, ["button", "button-block", "hidden", "button-text"]],
    ])("styles the %s end's %s button", (kind, action, expected) => {
        showEnd(kind);

        expect(actionClasses(action).filter(Boolean)).toEqual(expected);
    });
});
