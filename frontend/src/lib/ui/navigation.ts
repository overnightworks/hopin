// The room id and call link of the address, the document loads home, into a call and through sign-in, and the
// shallow rewrite of the address to the call link.

import { goto } from "$app/navigation";
import { resolve } from "$app/paths";

const HOME_PATH = "/";
const CALL_PATH_PREFIX = "c";
// The call address that names no room: the start link of a table of a registered app.
const START_LINK_SEGMENT = "new";

/** The table of a registered app that the document was opened at the start link of. */
export interface StartLink {
    app: string;
    context: string;
}

export function getRoomIdFromUrl() {
    const [, prefix, roomId] = location.pathname.split("/");
    return prefix === CALL_PATH_PREFIX && roomId && roomId !== START_LINK_SEGMENT ? roomId : null;
}

/** The app and key of the start link the document was loaded at, or null at any other address. */
export function getStartLinkFromUrl(): StartLink | null {
    const [, prefix, segment] = location.pathname.split("/");
    if (prefix !== CALL_PATH_PREFIX || segment !== START_LINK_SEGMENT) return null;
    const query = new URLSearchParams(location.search);
    const app = query.get("app");
    const context = query.get("context");
    return app && context ? { app, context } : null;
}

export function callUrl(roomId: string) {
    return `${location.origin}/${CALL_PATH_PREFIX}/${roomId}`;
}

// A shallow replace rewrites only the address bar: no load runs and the layout stays mounted.
export function showCallAddress(roomId: string) {
    return goto(resolve("/(call)/c/[room]", { room: roomId }), { shallow: true, replace: true });
}

export function goThroughSignIn() {
    location.reload();
}

export function goHome() {
    location.assign(HOME_PATH);
}

export function openCall(roomId: string) {
    location.assign(callUrl(roomId));
}
