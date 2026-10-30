import { ERROR_REASON, ROOM_CLOSED_REASON } from "./signaling.js";
import { getRoomIdFromUrl, goHome, openCall } from "#lib/ui/navigation.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";

export const END_KIND = {
    HOST_GONE: "host_gone",
    ENDED_BY_HOST: "ended_by_host",
    LEFT: "left",
    HOST_LEFT: "host_left",
    EXPIRED: "expired",
    LOCKED: "locked",
    FULL: "full",
    TOO_MANY_CALLS: "too_many_calls",
};

const ICON = { ENDED: "ended", HOURGLASS: "hourglass", LOCK: "lock", USERS: "users" };
const ACTION = { RETRY: "retry", HOME: "home", REJOIN: "rejoin" };

const OTHERS_STILL_TALKING = "The others are still talking.";

const END_VIEWS = {
    [END_KIND.HOST_GONE]: {
        icon: ICON.ENDED,
        title: "Call ended",
        text: ({ grace_seconds }) =>
            `The host didn't come back within ${grace_seconds} seconds, so the call ended for everyone.`,
        actions: [ACTION.HOME],
    },
    [END_KIND.ENDED_BY_HOST]: {
        icon: ICON.ENDED,
        title: "Call ended",
        text: () => "The host ended the call for everyone.",
        actions: [ACTION.HOME],
    },
    [END_KIND.LEFT]: {
        icon: ICON.ENDED,
        title: "You left the call",
        text: () => OTHERS_STILL_TALKING,
        actions: [ACTION.HOME, ACTION.REJOIN],
    },
    [END_KIND.HOST_LEFT]: {
        icon: ICON.ENDED,
        title: "You left the call",
        text: () => OTHERS_STILL_TALKING,
        actions: [ACTION.HOME],
    },
    [END_KIND.EXPIRED]: {
        icon: ICON.HOURGLASS,
        title: "This link has expired",
        text: () => "The call is over. Ask for a new link.",
        actions: [ACTION.HOME],
    },
    [END_KIND.LOCKED]: {
        icon: ICON.LOCK,
        warn: true,
        title: "Call is locked",
        text: () => "The host isn't letting anyone new in right now. Try again in a moment.",
        actions: [ACTION.RETRY, ACTION.HOME],
    },
    [END_KIND.FULL]: {
        icon: ICON.USERS,
        warn: true,
        title: "Call is full",
        pill: ({ capacity }) => `${capacity}/${capacity}`,
        text: ({ capacity }) => `A call has room for ${capacity} people.`,
        actions: [ACTION.HOME],
    },
    [END_KIND.TOO_MANY_CALLS]: {
        icon: ICON.USERS,
        warn: true,
        title: "Too many calls open",
        text: ({ limit }) => `Your account is in ${limit} calls. Leave one to join this one.`,
        actions: [ACTION.HOME],
    },
};

const REFUSAL_KINDS = {
    [ERROR_REASON.NOT_FOUND]: END_KIND.EXPIRED,
    [ERROR_REASON.LOCKED]: END_KIND.LOCKED,
    [ERROR_REASON.FULL]: END_KIND.FULL,
    [ERROR_REASON.TOO_MANY_CALLS]: END_KIND.TOO_MANY_CALLS,
};

const CLOSED_ROOM_KINDS = {
    [ROOM_CLOSED_REASON.HOST_GONE]: END_KIND.HOST_GONE,
    [ROOM_CLOSED_REASON.ENDED]: END_KIND.ENDED_BY_HOST,
};

const BUTTON_IDS = {
    [ACTION.RETRY]: "endRetryBtn",
    [ACTION.HOME]: "endHomeBtn",
    [ACTION.REJOIN]: "endRejoinBtn",
};

export function endKindOfRefusal(reason) {
    return REFUSAL_KINDS[reason] ?? null;
}

export function endKindOfClosedRoom(reason) {
    return CLOSED_ROOM_KINDS[reason];
}

function showOnly(elements, shown) {
    for (const element of elements) {
        element.classList.toggle("hidden", !shown(element));
    }
}

function renderActions(actions) {
    const [primary] = actions;
    for (const [action, id] of Object.entries(BUTTON_IDS)) {
        const button = document.getElementById(id);
        const isPrimary = action === primary;
        button.classList.toggle("hidden", !actions.includes(action));
        button.classList.toggle("button-primary", isPrimary);
        button.classList.toggle("button-lg", isPrimary);
        button.classList.toggle("button-text", !isPrimary);
    }
}

export function showEnd(kind, frame = {}) {
    const view = END_VIEWS[kind];
    const pill = document.getElementById("endPill");
    showOnly(document.querySelectorAll("[data-end-icon]"), (icon) => icon.dataset.endIcon === view.icon);
    document.getElementById("endIcon").classList.toggle("warn", Boolean(view.warn));
    document.getElementById("endTitle").textContent = view.title;
    document.getElementById("endText").textContent = view.text(frame);
    pill.textContent = view.pill?.(frame) ?? "";
    pill.classList.toggle("hidden", !view.pill);
    renderActions(view.actions);
    showScreen(SCREEN.END);
}

export function initEnd() {
    const rejoin = () => openCall(getRoomIdFromUrl());
    document.getElementById(BUTTON_IDS[ACTION.HOME]).addEventListener("click", goHome);
    document.getElementById(BUTTON_IDS[ACTION.REJOIN]).addEventListener("click", rejoin);
    document.getElementById(BUTTON_IDS[ACTION.RETRY]).addEventListener("click", rejoin);
}
