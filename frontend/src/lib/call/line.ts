// What a chat line may be: 1 to the limit's code points once the trimmed characters are cut from its ends, with none
// of the refused characters, and how fast one sender's lines may follow each other. The server's `clean_line` applies
// the same sets from the same owner.

import {
    LINE_BURST,
    LINE_MAX_CHARACTERS,
    LINE_REFUSED_CHARACTERS,
    LINE_TRIMMED_CHARACTERS,
    LINES_PER_SECOND,
} from "#lib/wire.ts";

const TRIMMED = new Set(LINE_TRIMMED_CHARACTERS);
const REFUSED = new Set(LINE_REFUSED_CHARACTERS);

export const LINE_REFUSAL = {
    TOO_LONG: "too-long",
    REFUSED_CHARACTER: "refused-character",
} as const;
export type LineRefusal = (typeof LINE_REFUSAL)[keyof typeof LINE_REFUSAL];

function trimmedCharacters(text: string): string[] {
    const characters = Array.from(text);
    let start = 0;
    let end = characters.length;
    while (start < end && TRIMMED.has(characters[start] as string)) start++;
    while (end > start && TRIMMED.has(characters[end - 1] as string)) end--;
    return characters.slice(start, end);
}

function refusalOf(line: readonly string[]): LineRefusal | null {
    if (line.length > LINE_MAX_CHARACTERS) return LINE_REFUSAL.TOO_LONG;
    return line.some((character) => REFUSED.has(character)) ? LINE_REFUSAL.REFUSED_CHARACTER : null;
}

/** Why the text is no line although it holds one, or null when it is one or is blank. */
export function lineRefusal(text: string): LineRefusal | null {
    return refusalOf(trimmedCharacters(text));
}

/** The line the text makes, or null when it is no line. */
export function cleanLine(text: string): string | null {
    const line = trimmedCharacters(text);
    return line.length === 0 || refusalOf(line) !== null ? null : line.join("");
}

const EPSILON = 1e-9;

/** The lines one sender may still write at once: it holds `capacity` lines and refills at the limits' rate. Every receiver counts a sender's lines with one, and a sender paces its own with one. */
export class LineBudget {
    readonly #now: () => number;
    readonly #capacity: number;
    #lines: number;
    #countedAt: number;

    constructor(now: () => number, capacity = LINE_BURST) {
        this.#now = now;
        this.#capacity = capacity;
        this.#lines = capacity;
        this.#countedAt = now();
    }

    /** Spends one line and says whether the budget held one. */
    spend(): boolean {
        this.#refill();
        if (this.#lines < 1 - EPSILON) return false;
        this.#lines -= 1;
        return true;
    }

    /** Whether the budget holds `count` whole lines now. */
    holds(count: number): boolean {
        this.#refill();
        return this.#lines >= count - EPSILON;
    }

    /** The milliseconds until the budget holds `count` whole lines, no more than its capacity; none when it holds them now. */
    waitMilliseconds(count = 1): number {
        this.#refill();
        return Math.max(0, Math.ceil(((count - this.#lines) / LINES_PER_SECOND) * 1000));
    }

    #refill() {
        const now = this.#now();
        const elapsedSeconds = Math.max(0, now - this.#countedAt) / 1000;
        this.#lines = Math.min(this.#capacity, this.#lines + elapsedSeconds * LINES_PER_SECOND);
        this.#countedAt = now;
    }
}
