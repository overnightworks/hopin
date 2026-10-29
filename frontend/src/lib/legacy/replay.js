import { DESCRIPTION_TYPES, REPLAY_LOG_LIMIT, SUPERSEDED_BY_A_DESCRIPTION } from "./wire.js";

export { REPLAY_LOG_LIMIT };

const descriptionTypes = new Set(DESCRIPTION_TYPES);
const supersededByADescription = new Set(SUPERSEDED_BY_A_DESCRIPTION);

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
        if (descriptionTypes.has(message.type)) this.#dropSupersededBy(message);
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
                !(supersededByADescription.has(frame.type) && frame.target === description.target),
        );
    }
}
