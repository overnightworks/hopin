import { goto } from "$app/navigation";
import { resolve } from "$app/paths";

const HOME_PATH = "/";
const CALL_PATH_PREFIX = "c";

export function getRoomIdFromUrl() {
    const [, prefix, roomId] = location.pathname.split("/");
    return prefix === CALL_PATH_PREFIX && roomId ? roomId : null;
}

export function callUrl(roomId: string) {
    return `${location.origin}/${CALL_PATH_PREFIX}/${roomId}`;
}

// A shallow replace rewrites only the address bar: no load runs and the layout stays mounted.
export function showCallAddress(roomId: string) {
    return goto(resolve("/c/[room]", { room: roomId }), { shallow: true, replace: true });
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
