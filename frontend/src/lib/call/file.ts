// What a posted file may be and how the page treats it: a picture is shown only when its first bytes are a raster
// format the page knows (never SVG, which can carry script), every other file is offered as a download under a
// sanitized name. What the sender announces about a file is a hint; only the bytes decide.

import {
    FILE_MAX_BYTES,
    FILE_NAME_MAX_CHARACTERS,
    LINE_REFUSED_CHARACTERS,
    PICTURE_AUTO_FETCH_BYTES,
} from "#lib/wire.ts";

/** The bytes at the start of a file the raster check reads. */
export const PICTURE_HEAD_BYTES = 12;

/** The type of a blob offered only as a download: the browser never renders or runs it. */
export const DOWNLOAD_TYPE = "application/octet-stream";

const FALLBACK_NAME = "datei";

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG_SIGNATURE = [0xff, 0xd8, 0xff];
const GIF_SIGNATURES = ["GIF87a", "GIF89a"];
const RIFF = "RIFF";
const WEBP = "WEBP";
const WEBP_FORMAT_OFFSET = 8;

/** The raster types a picture may have, by the type its bytes show. */
export const PICTURE_TYPE = {
    PNG: "image/png",
    JPEG: "image/jpeg",
    GIF: "image/gif",
    WEBP: "image/webp",
} as const;
export type PictureType = (typeof PICTURE_TYPE)[keyof typeof PICTURE_TYPE];

const PICTURE_TYPES: ReadonlySet<string> = new Set(Object.values(PICTURE_TYPE));

// Path separators, the controls the line rule refuses, and the characters that reorder or hide text around a name.
const REFUSED_IN_NAME = new Set(
    Array.from(
        `${LINE_REFUSED_CHARACTERS}/\\:\u061c\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff`,
    ),
);

function startsWith(head: Uint8Array, signature: readonly number[], offset = 0): boolean {
    return signature.every((byte, index) => head[offset + index] === byte);
}

function textAt(head: Uint8Array, text: string, offset = 0): boolean {
    return startsWith(head, Array.from(new TextEncoder().encode(text)), offset);
}

/** The raster type the first bytes show, or null when they show none (SVG included). */
export function pictureTypeOf(head: Uint8Array): PictureType | null {
    if (startsWith(head, PNG_SIGNATURE)) return PICTURE_TYPE.PNG;
    if (startsWith(head, JPEG_SIGNATURE)) return PICTURE_TYPE.JPEG;
    if (GIF_SIGNATURES.some((signature) => textAt(head, signature))) return PICTURE_TYPE.GIF;
    if (textAt(head, RIFF) && textAt(head, WEBP, WEBP_FORMAT_OFFSET)) return PICTURE_TYPE.WEBP;
    return null;
}

/** Whether a sender announced a file as a raster picture small enough to be fetched at once. */
export function isFetchedAtOnce(announcedType: string, size: number): boolean {
    return PICTURE_TYPES.has(announcedType) && size <= PICTURE_AUTO_FETCH_BYTES;
}

export const FILE_REFUSAL = {
    EMPTY: "empty",
    TOO_LARGE: "too-large",
} as const;
export type FileRefusal = (typeof FILE_REFUSAL)[keyof typeof FILE_REFUSAL];

/** Why a file of this size cannot be posted or received, or null when it holds a byte and is within the per-file cap. */
export function fileRefusal(size: number): FileRefusal | null {
    if (size < 1) return FILE_REFUSAL.EMPTY;
    return size > FILE_MAX_BYTES ? FILE_REFUSAL.TOO_LARGE : null;
}

/** The name the page shows and offers for a download: no path, no control or reordering character, at most the limit's code points; a name left empty is "datei". */
export function safeFileName(name: string): string {
    const kept = Array.from(name)
        .filter((character) => !REFUSED_IN_NAME.has(character))
        .join("")
        .trim();
    const cut = Array.from(kept).slice(0, FILE_NAME_MAX_CHARACTERS).join("").trim();
    return cut === "" || /^\.+$/.test(cut) ? FALLBACK_NAME : cut;
}
