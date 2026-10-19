export const REPLAY_LOG_LIMIT = 256;

const DESCRIPTION_TYPES = new Set(["offer", "answer"]);
const SUPERSEDED_BY_A_DESCRIPTION = new Set(["offer", "answer", "ice_candidate"]);

export class SentLog {
    #limit;
    #frames = [];
    #lastSeq = 0;
    #floor = 0;

    constructor(limit = REPLAY_LOG_LIMIT) {
        this.#limit = limit;
    }

    get floor() {
        return this.#floor;
    }

    append(message) {
        if (DESCRIPTION_TYPES.has(message.type)) this.#dropSupersededBy(message);
        this.#lastSeq += 1;
        const frame = { ...message, seq: this.#lastSeq };
        this.#frames.push(frame);
        while (this.#frames.length > this.#limit) {
            this.#floor = this.#frames.shift().seq;
        }
        return frame;
    }

    acknowledge(receivedSeq) {
        this.#frames = this.#frames.filter((frame) => frame.seq > receivedSeq);
    }

    unacknowledged() {
        return [...this.#frames];
    }

    #dropSupersededBy(description) {
        this.#frames = this.#frames.filter(
            (frame) =>
                !(SUPERSEDED_BY_A_DESCRIPTION.has(frame.type) && frame.target === description.target),
        );
    }
}
