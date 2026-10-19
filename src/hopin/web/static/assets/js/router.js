export const SCREEN = {
    HOME: "home",
    CALL: "call",
    END: "end",
};

const HOME_PATH = "/";
const CALL_PATH_PREFIX = "c";

export function getRoomIdFromUrl() {
    const [, prefix, roomId] = location.pathname.split("/");
    return prefix === CALL_PATH_PREFIX && roomId ? roomId : null;
}

export function callUrl(roomId) {
    return `${location.origin}/${CALL_PATH_PREFIX}/${roomId}`;
}

export function showScreen(screen) {
    for (const element of document.querySelectorAll("[data-screen]")) {
        element.classList.toggle("hidden", element.dataset.screen !== screen);
    }
}

export function goHome() {
    location.assign(HOME_PATH);
}

export function openCall(roomId) {
    location.assign(callUrl(roomId));
}
