/** The table's side of the handshake with the framed Skat page: it answers the page's `ready` with a `hello`. */

const PROTOCOL_VERSION = 1;
const READY = "ready";
const HELLO = "hello";
const READY_KEYS = ["v", "type", "table"];

export interface Hello {
    v: typeof PROTOCOL_VERSION;
    type: typeof HELLO;
    attachment: number;
    attempt: null;
}

export interface Greeting {
    hello: Hello;
    targetOrigin: string;
}

/** What the table shows now: the attachment's number and the id the server named for it. */
export interface CurrentTable {
    attachment: number;
    table: string;
}

/** A message event as far as the check reads it: where it came from, and whether from the frame's own window. */
export interface Received {
    origin: string;
    fromFrame: boolean;
    data: unknown;
}

function isReadyFor(data: unknown, table: string): boolean {
    if (typeof data !== "object" || data === null || Object.getPrototypeOf(data) !== Object.prototype) return false;
    const keys = Reflect.ownKeys(data);
    // Exactly three keys and a `table` equal to the server's 32 hex digits bound the message's size.
    if (keys.length !== READY_KEYS.length || !READY_KEYS.every((key) => keys.includes(key))) return false;
    const ready = data as { v: unknown; type: unknown; table: unknown };
    return ready.v === PROTOCOL_VERSION && ready.type === READY && ready.table === table;
}

/** The `hello` a message earns, or null when it is to be ignored: it counts only from the frame's origin and window,
 * as a plain `ready` for the table that is attached now. */
export function greetingFor(received: Received, frameOrigin: string, current: CurrentTable | null): Greeting | null {
    if (current === null || !received.fromFrame || received.origin !== frameOrigin) return null;
    if (!isReadyFor(received.data, current.table)) return null;
    return {
        hello: { v: PROTOCOL_VERSION, type: HELLO, attachment: current.attachment, attempt: null },
        targetOrigin: frameOrigin,
    };
}
