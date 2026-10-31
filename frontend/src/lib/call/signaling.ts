import { type LoggedMessage, type OutgoingMessage, type SentFrame, SentLog } from "#lib/call/replay.ts";
import { state } from "#lib/call/state.ts";
import { getRoomIdFromUrl } from "#lib/ui/navigation.ts";
import { setStatus } from "#lib/ui/status.svelte.ts";
import {
    ERROR_REASON,
    type RoomClosedFrame,
    SESSION_ENDED_CLOSE_CODE,
    SIGNAL_TYPE,
    WEBSOCKET_PATH,
} from "#lib/wire.ts";

export interface ServerFrame {
    type: SIGNAL_TYPE;
    seq?: number;
    resume_token?: string;
    participant_id?: string;
    last_seq?: number;
    reason?: unknown;
    [field: string]: unknown;
}

// Method syntax keeps parameters bivariant, so a handler typed for one frame shape registers without a cast.
type Handler = { handle(data: ServerFrame): void }["handle"];

interface Seat {
    participantId: string | undefined;
    token: string | undefined;
}

const NORMAL_CLOSURE_CODE = 1000;
export const RECONNECT_DELAYS_MS = [1000, 2000, 4000, 8000, 8000, 6000];

const handlersByType = new Map<string, Handler[]>();
let socket: WebSocket | null = null;
let socketIsLive = false;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let seat: Seat | null = null;
let sentLog = new SentLog();
let lastDispatchedSeq = 0;
let sessionLost = () => {};
let sessionEnded = () => {};

function addHandler(type: string, handler: Handler) {
    handlersByType.set(type, [...(handlersByType.get(type) ?? []), handler]);
}

export function on(type: SIGNAL_TYPE.ROOM_CLOSED, handler: (frame: RoomClosedFrame) => void): void;
export function on(type: SIGNAL_TYPE, handler: Handler): void;
export function on(type: SIGNAL_TYPE, handler: Handler | ((frame: RoomClosedFrame) => void)) {
    addHandler(type, handler);
}

// The server the page trusts sends the frames a feature registers for, so the handler's frame type is taken on trust.
export function onFeatureFrame(type: string, handler: (frame: never) => void) {
    addHandler(type, handler);
}

export function onSessionLost(callback: () => void) {
    sessionLost = callback;
}

export function onSessionEnded(callback: () => void) {
    sessionEnded = callback;
}

export function send(message: OutgoingMessage) {
    sendLogged(message);
}

export function sendFeatureFrame(message: LoggedMessage) {
    sendLogged(message);
}

function sendLogged(message: LoggedMessage) {
    if (socket === null) {
        throw new Error("No signaling session: connect() before send()");
    }
    const frame = sentLog.append(message);
    if (socketIsLive) socket.send(JSON.stringify(frame));
}

export function isConnected() {
    return socket !== null;
}

function dispatch(data: ServerFrame) {
    for (const handler of handlersByType.get(data.type) ?? []) {
        handler(data);
    }
}

function joinMessage(): OutgoingMessage {
    const message: OutgoingMessage = { type: SIGNAL_TYPE.JOIN };
    const urlRoomId = getRoomIdFromUrl();
    if (urlRoomId) {
        message.room_id = urlRoomId;
    } else if (state.roomId) {
        message.room_id = state.roomId;
    }
    return message;
}

function resumeMessage(heldSeat: Seat): OutgoingMessage {
    return {
        type: SIGNAL_TYPE.RESUME,
        participant_id: heldSeat.participantId,
        token: heldSeat.token,
        last_seq: lastDispatchedSeq,
        sent_floor: sentLog.floor,
    };
}

function writeFirstFrame(current: WebSocket, message: OutgoingMessage | SentFrame) {
    current.send(JSON.stringify(message));
}

function startWriting(current: WebSocket) {
    socketIsLive = true;
    for (const frame of sentLog.unacknowledged()) {
        writeFirstFrame(current, frame);
    }
}

function forgetSeat() {
    seat = null;
    sentLog = new SentLog();
    lastDispatchedSeq = 0;
}

function handleOpen(current: WebSocket) {
    setStatus("Connected - joining...", "connecting");
    if (seat) {
        writeFirstFrame(current, resumeMessage(seat));
    } else {
        writeFirstFrame(current, joinMessage());
        startWriting(current);
    }
}

function handleResumed(current: WebSocket, heldSeat: Seat, data: ServerFrame) {
    heldSeat.token = data.resume_token;
    sentLog.acknowledge(data.last_seq as number);
    startWriting(current);
    state.reconnectAttempt = 0;
    setStatus("In call", "connected");
}

function fallBackToJoin(current: WebSocket) {
    forgetSeat();
    sessionLost();
    startWriting(current);
    send(joinMessage());
}

function keepSeat(data: ServerFrame) {
    seat = { participantId: data.participant_id, token: data.resume_token };
}

function isAlreadyDispatched(data: ServerFrame) {
    if (data.seq === undefined) return false;
    if (data.seq <= lastDispatchedSeq) return true;
    lastDispatchedSeq = data.seq;
    return false;
}

function handleMessage(current: WebSocket, event: MessageEvent<string>) {
    const data = JSON.parse(event.data) as ServerFrame;
    if (data.type === SIGNAL_TYPE.RESUMED) {
        handleResumed(current, seat as Seat, data);
    } else if (data.type === SIGNAL_TYPE.ERROR && data.reason === ERROR_REASON.RESUME_REFUSED) {
        fallBackToJoin(current);
    } else if (!isAlreadyDispatched(data)) {
        if (data.resume_token) keepSeat(data);
        dispatch(data);
    }
}

function endSession() {
    socket = null;
    socketIsLive = false;
    forgetSeat();
}

function scheduleReconnect(delay: number) {
    state.reconnectAttempt++;
    setStatus(`Reconnecting in ${String(delay / 1000)}s...`, "connecting");
    reconnectTimer = setTimeout(connect, delay);
}

function handleClose(event: CloseEvent) {
    socketIsLive = false;
    if (state.intentionalLeave) return;
    if (event.code === SESSION_ENDED_CLOSE_CODE) {
        endSession();
        sessionEnded();
        return;
    }
    const heldSeat = seat !== null;
    const delay = RECONNECT_DELAYS_MS[state.reconnectAttempt];
    if (heldSeat && delay !== undefined) {
        scheduleReconnect(delay);
        return;
    }
    endSession();
    sessionLost();
    if (heldSeat) {
        setStatus("Connection lost", "error");
    } else {
        setStatus("Disconnected", "disconnected");
    }
}

export function connect() {
    const scheme = location.protocol.replace("http", "ws");
    const current = new WebSocket(`${scheme}//${location.host}${WEBSOCKET_PATH}`);
    socket = current;

    const ifCurrent =
        <Incoming>(handler: (event: Incoming) => void) =>
        (event: Incoming) => {
            if (current === socket) handler(event);
        };
    current.onopen = ifCurrent(() => {
        handleOpen(current);
    });
    current.onmessage = ifCurrent((event: MessageEvent<string>) => {
        handleMessage(current, event);
    });
    current.onclose = ifCurrent(handleClose);
    current.onerror = ifCurrent(() => {
        setStatus("Connection error", "error");
    });
}

export function disconnect() {
    if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    socket?.close(NORMAL_CLOSURE_CODE);
    endSession();
}
