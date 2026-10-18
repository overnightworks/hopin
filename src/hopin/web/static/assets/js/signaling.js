import { state } from "./state.js";
import { setStatus } from "./status.js";

export const SIGNAL_TYPE = {
    OFFER: "offer",
    ANSWER: "answer",
    ICE_CANDIDATE: "ice_candidate",
    JOIN: "join",
    ROOM_CREATED: "room_created",
    PARTICIPANT_JOINED: "participant_joined",
    PARTICIPANT_LEFT: "participant_left",
    EXISTING_PARTICIPANTS: "existing_participants",
    ROOM_CLOSED: "room_closed",
    ROOM_LOCKED: "room_locked",
    ROOM_UNLOCKED: "room_unlocked",
    LOCK_ROOM: "lock_room",
    UNLOCK_ROOM: "unlock_room",
    CHAT: "chat",
    ERROR: "error",
};

const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000];

const handlersByType = new Map();
let socket = null;
let reconnectTimer = null;
let beforeReconnect = () => {};

export function on(type, handler) {
    handlersByType.set(type, [...(handlersByType.get(type) ?? []), handler]);
}

export function onBeforeReconnect(callback) {
    beforeReconnect = callback;
}

export function send(message) {
    socket?.send(JSON.stringify(message));
}

export function isConnected() {
    return socket !== null;
}

export function getRoomIdFromUrl() {
    const parts = location.pathname.split("/");
    if (parts.length >= 3 && parts[1] === "join") {
        return parts[2];
    }
    return null;
}

function dispatch(data) {
    for (const handler of handlersByType.get(data.type) ?? []) {
        handler(data);
    }
}

function sendJoin() {
    const joinMessage = { type: SIGNAL_TYPE.JOIN };
    const urlRoomId = getRoomIdFromUrl();
    if (urlRoomId) {
        joinMessage.room_id = urlRoomId;
    } else if (state.roomId) {
        joinMessage.room_id = state.roomId;
    }
    send(joinMessage);
}

function handleMessage(event) {
    dispatch(JSON.parse(event.data));
}

function scheduleReconnect() {
    const delay = RECONNECT_DELAYS_MS[state.reconnectAttempt];
    state.reconnectAttempt++;
    setStatus(`Reconnecting in ${delay / 1000}s...`, "connecting");
    reconnectTimer = setTimeout(() => {
        beforeReconnect();
        connect();
    }, delay);
}

function handleClose() {
    if (state.intentionalLeave) return;
    if (state.roomId && state.reconnectAttempt < RECONNECT_DELAYS_MS.length) {
        scheduleReconnect();
    } else if (state.roomId) {
        setStatus("Connection lost", "error");
    } else {
        setStatus("Disconnected", "disconnected");
    }
}

export function connect() {
    const scheme = location.protocol.replace("http", "ws");
    socket = new WebSocket(`${scheme}//${location.host}/ws?id=${crypto.randomUUID()}`);

    socket.onopen = () => {
        setStatus("Connected - joining...", "connecting");
        sendJoin();
    };
    socket.onmessage = handleMessage;
    socket.onclose = handleClose;
    socket.onerror = () => {
        setStatus("Connection error", "error");
    };
}

export function disconnect() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    socket?.close();
    socket = null;
}
