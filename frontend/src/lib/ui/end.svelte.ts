import { ERROR_REASON, type ErrorFrame, ROOM_CLOSED_REASON, type RoomClosedFrame } from "#lib/wire.ts";
import { getRoomIdFromUrl, openCall } from "#lib/ui/navigation.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";
import { words } from "#lib/words/de.ts";

export enum END_KIND {
    HOST_GONE = "host_gone",
    ENDED_BY_HOST = "ended_by_host",
    LEFT = "left",
    HOST_LEFT = "host_left",
    EXPIRED = "expired",
    LOCKED = "locked",
    FULL = "full",
    TOO_MANY_CALLS = "too_many_calls",
}

export enum END_ICON {
    ENDED = "ended",
    HOURGLASS = "hourglass",
    LOCK = "lock",
    USERS = "users",
}

export enum END_ACTION {
    RETRY = "retry",
    HOME = "home",
    REJOIN = "rejoin",
}

export type EndFrame = Partial<Pick<RoomClosedFrame, "grace_seconds" | "host_name">> &
    Pick<ErrorFrame, "capacity" | "limit">;

type EndView = {
    icon: END_ICON;
    warn?: true;
    title: string;
    text: (frame: EndFrame) => string;
    pill?: (frame: EndFrame) => string;
    actions: readonly END_ACTION[];
};

const END_VIEWS: Record<END_KIND, EndView> = {
    [END_KIND.HOST_GONE]: {
        icon: END_ICON.ENDED,
        title: words["end.callEndedTitle"],
        text: ({ host_name, grace_seconds }) => {
            const seconds = String(grace_seconds);
            return host_name
                ? words["end.hostGoneNamedText"]({ name: host_name, seconds })
                : words["end.hostGoneUnnamedText"]({ seconds });
        },
        actions: [END_ACTION.HOME],
    },
    [END_KIND.ENDED_BY_HOST]: {
        icon: END_ICON.ENDED,
        title: words["end.callEndedTitle"],
        text: ({ host_name }) =>
            host_name ? words["end.endedByHostNamedText"]({ name: host_name }) : words["end.endedByHostUnnamedText"],
        actions: [END_ACTION.HOME],
    },
    [END_KIND.LEFT]: {
        icon: END_ICON.ENDED,
        title: words["end.leftTitle"],
        text: () => words["end.leftText"],
        actions: [END_ACTION.HOME, END_ACTION.REJOIN],
    },
    [END_KIND.HOST_LEFT]: {
        icon: END_ICON.ENDED,
        title: words["end.leftTitle"],
        text: () => words["end.leftText"],
        actions: [END_ACTION.HOME],
    },
    [END_KIND.EXPIRED]: {
        icon: END_ICON.HOURGLASS,
        title: words["end.expiredTitle"],
        text: () => words["end.expiredText"],
        actions: [END_ACTION.HOME],
    },
    [END_KIND.LOCKED]: {
        icon: END_ICON.LOCK,
        warn: true,
        title: words["end.lockedTitle"],
        text: () => words["end.lockedText"],
        actions: [END_ACTION.RETRY, END_ACTION.HOME],
    },
    [END_KIND.FULL]: {
        icon: END_ICON.USERS,
        warn: true,
        title: words["end.fullTitle"],
        pill: ({ capacity }) => words["end.fullPill"]({ capacity: String(capacity) }),
        text: ({ capacity }) => words["end.fullText"]({ capacity: String(capacity) }),
        actions: [END_ACTION.HOME],
    },
    [END_KIND.TOO_MANY_CALLS]: {
        icon: END_ICON.USERS,
        warn: true,
        title: words["end.tooManyCallsTitle"],
        text: ({ limit }) => words["end.tooManyCallsText"]({ limit: String(limit) }),
        actions: [END_ACTION.HOME],
    },
};

const REFUSAL_KINDS: Partial<Record<ERROR_REASON, END_KIND>> = {
    [ERROR_REASON.NOT_FOUND]: END_KIND.EXPIRED,
    [ERROR_REASON.LOCKED]: END_KIND.LOCKED,
    [ERROR_REASON.FULL]: END_KIND.FULL,
    [ERROR_REASON.TOO_MANY_CALLS]: END_KIND.TOO_MANY_CALLS,
};

const CLOSED_ROOM_KINDS: Record<ROOM_CLOSED_REASON, END_KIND> = {
    [ROOM_CLOSED_REASON.HOST_GONE]: END_KIND.HOST_GONE,
    [ROOM_CLOSED_REASON.ENDED]: END_KIND.ENDED_BY_HOST,
};

export function endKindOfRefusal(reason: ERROR_REASON) {
    return REFUSAL_KINDS[reason] ?? null;
}

export function endKindOfClosedRoom(reason: ROOM_CLOSED_REASON) {
    return CLOSED_ROOM_KINDS[reason];
}

const shownEnd: { kind: END_KIND | null; frame: EndFrame } = $state({ kind: null, frame: {} });

export function showEnd(kind: END_KIND, frame: EndFrame = {}) {
    shownEnd.kind = kind;
    shownEnd.frame = frame;
    showScreen(SCREEN.END);
}

export function shownEndView() {
    const { kind, frame } = shownEnd;
    if (kind === null) {
        return {
            icon: null,
            isWarning: false,
            title: words["end.callEndedTitle"],
            text: "",
            pill: null,
            actions: [] as readonly END_ACTION[],
        };
    }
    const view = END_VIEWS[kind];
    return {
        icon: view.icon,
        isWarning: Boolean(view.warn),
        title: view.title,
        text: view.text(frame),
        pill: view.pill?.(frame) ?? null,
        actions: view.actions,
    };
}

export function actionClasses(action: END_ACTION) {
    const { actions } = shownEndView();
    return [
        "button",
        "button-block",
        !actions.includes(action) && "hidden",
        ...(actions[0] === action ? ["button-primary", "button-lg"] : ["button-text"]),
    ];
}

export function rejoinCall() {
    const roomId = getRoomIdFromUrl();
    if (roomId !== null) {
        openCall(roomId);
    }
}
