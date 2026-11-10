import type { OutgoingFrame } from "#lib/call/feature-port.ts";
import type { CurrentTable } from "./messages.ts";
import { SKAT_SIGNAL } from "./wire.ts";

export type TableFrame = { revision: number; opener_id: string | null } & (
    { attachment: number; table: string; src: string } | { attachment: null; table: null; src: null }
);

export interface TableStage {
    show(src: string, table: Table): void;
    hide(): void;
}

/** The room's table as the server last said it: frames of an older revision are dropped, and the frame of the table
 * is rebuilt only for another attachment, never for a change of its opener. */
export class Table {
    readonly #stage: TableStage;
    readonly #send: (frame: OutgoingFrame) => void;
    #highestRevision = -1;
    #seatId = $state("");
    #isHost = $state(false);
    #openerId = $state<string | null>(null);
    #attachment = $state<number | null>(null);
    #tableId = $state<string | null>(null);

    constructor(stage: TableStage, send: (frame: OutgoingFrame) => void) {
        this.#stage = stage;
        this.#send = send;
    }

    /** Whether this seat may remove the table: the host, the opener, or anyone once the opener has left. */
    get mayRemove() {
        return this.#isHost || this.#openerId === this.#seatId || this.#openerId === null;
    }

    /** The table attached now, or null while none is. */
    get current(): CurrentTable | null {
        return this.#attachment === null ? null : { attachment: this.#attachment, table: this.#tableId as string };
    }

    seated(seatId: string, isHost: boolean) {
        this.#seatId = seatId;
        this.#isHost = isHost;
    }

    receive(frame: TableFrame) {
        if (frame.revision < this.#highestRevision) return;
        this.#highestRevision = frame.revision;
        this.#openerId = frame.opener_id;
        if (frame.attachment === null) {
            if (this.#attachment !== null) this.#stage.hide();
            this.#attachment = null;
            this.#tableId = null;
            return;
        }
        if (frame.attachment === this.#attachment) return;
        this.#attachment = frame.attachment;
        this.#tableId = frame.table;
        this.#stage.show(frame.src, this);
    }

    remove() {
        if (this.#attachment !== null) this.#send({ type: SKAT_SIGNAL.SKAT_DETACH, attachment: this.#attachment });
    }

    reset() {
        if (this.#attachment !== null) this.#stage.hide();
        this.#attachment = null;
        this.#tableId = null;
        this.#openerId = null;
        this.#highestRevision = -1;
    }
}
