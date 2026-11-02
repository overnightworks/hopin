import { DESCRIPTION_TYPES, REPLAY_LOG_LIMIT, SUPERSEDED_BY_A_DESCRIPTION, type SIGNAL_TYPE } from "#lib/wire.ts";

export interface OutgoingMessage {
    type: SIGNAL_TYPE;
    target?: string;
    [field: string]: unknown;
}

export interface LoggedMessage {
    type: string;
    target?: string;
    [field: string]: unknown;
}

export type SentFrame = LoggedMessage & { seq: number };

const descriptionTypes = new Set<string>(DESCRIPTION_TYPES);
const supersededByADescription = new Set<string>(SUPERSEDED_BY_A_DESCRIPTION);

export class SentLog {
    readonly #limit: number;
    #frames: SentFrame[] = [];
    #lastSeq = 0;
    #floor = 0;

    constructor(limit = REPLAY_LOG_LIMIT) {
        this.#limit = limit;
    }

    get floor() {
        return this.#floor;
    }

    append(message: LoggedMessage): SentFrame {
        if (descriptionTypes.has(message.type)) this.#dropSupersededBy(message);
        this.#lastSeq += 1;
        const frame = { ...message, seq: this.#lastSeq };
        this.#frames.push(frame);
        while (this.#frames.length > this.#limit) {
            this.#floor = (this.#frames.shift() as SentFrame).seq;
        }
        return frame;
    }

    acknowledge(receivedSeq: number) {
        this.#frames = this.#frames.filter((frame) => frame.seq > receivedSeq);
    }

    unacknowledged(): SentFrame[] {
        return [...this.#frames];
    }

    #dropSupersededBy(description: LoggedMessage) {
        this.#frames = this.#frames.filter(
            (frame) => !(supersededByADescription.has(frame.type) && frame.target === description.target),
        );
    }
}
