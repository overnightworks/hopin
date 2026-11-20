// What a chat line may be: 1 to the limit's code points once the trimmed characters are cut from its ends, with none
// of the refused characters. The server's `clean_line` applies the same sets from the same owner.

import { LINE_MAX_CHARACTERS, LINE_REFUSED_CHARACTERS, LINE_TRIMMED_CHARACTERS } from "#lib/wire.ts";

const TRIMMED = new Set(LINE_TRIMMED_CHARACTERS);
const REFUSED = new Set(LINE_REFUSED_CHARACTERS);

/** The line the text makes, or null when it is no line. */
export function cleanLine(text: string): string | null {
    const characters = Array.from(text);
    let start = 0;
    let end = characters.length;
    while (start < end && TRIMMED.has(characters[start] as string)) start++;
    while (end > start && TRIMMED.has(characters[end - 1] as string)) end--;
    const line = characters.slice(start, end);
    if (line.length === 0 || line.length > LINE_MAX_CHARACTERS) return null;
    if (line.some((character) => REFUSED.has(character))) return null;
    return line.join("");
}
