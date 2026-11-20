// What a posted file may be and how the page treats it: a picture is shown only when its first bytes are a raster
// format the page knows (never SVG, which can carry script), a song or video is played only when its first bytes are
// a container the page knows, every other file is offered as a download under a sanitized name. What the sender
// announces about a file is a hint; only the bytes decide.

import {
    FILE_MAX_BYTES,
    FILE_NAME_MAX_CHARACTERS,
    LINE_REFUSED_CHARACTERS,
    PICTURE_AUTO_FETCH_BYTES,
} from "#lib/wire.ts";

/** The bytes at the start of a file the raster and the container checks read. */
export const FILE_HEAD_BYTES = 12;

/** The type of a blob offered only as a download: the browser never renders or runs it. */
export const DOWNLOAD_TYPE = "application/octet-stream";

const FALLBACK_NAME = "datei";

const PNG_SIGNATURE = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
const JPEG_SIGNATURE = [0xff, 0xd8, 0xff];
const GIF_SIGNATURES = ["GIF87a", "GIF89a"];
const RIFF = "RIFF";
const WEBP = "WEBP";
const RIFF_FORMAT_OFFSET = 8;
const WAVE = "WAVE";
const OGG = "OggS";
const EBML_SIGNATURE = [0x1a, 0x45, 0xdf, 0xa3];
const ID3 = "ID3";
const FTYP = "ftyp";
const FTYP_BRAND_OFFSET = 8;
const FTYP_BOX_TYPE_OFFSET = 4;
const FRAME_SYNC_FIRST_BYTE = 0xff;
const FRAME_SYNC_SECOND_BYTE_MASK = 0xe0;
// The second byte of a frame header: version in bits 4-3 (01 is reserved), layer in bits 2-1 (00 is ADTS).
const ADTS_SECOND_BYTE_MASK = 0xf6;
const ADTS_SECOND_BYTE = 0xf0;
const MPEG_VERSION_SHIFT = 3;
const MPEG_LAYER_SHIFT = 1;
const TWO_BIT_MASK = 0b11;
const RESERVED_MPEG_VERSION = 0b01;

/** The raster types a picture may have, by the type its bytes show. */
export const PICTURE_TYPE = {
    PNG: "image/png",
    JPEG: "image/jpeg",
    GIF: "image/gif",
    WEBP: "image/webp",
} as const;
export type PictureType = (typeof PICTURE_TYPE)[keyof typeof PICTURE_TYPE];

const PICTURE_TYPES: ReadonlySet<string> = new Set(Object.values(PICTURE_TYPE));

/** The song and video types a file may have, by the type its bytes show. */
export const MEDIA_TYPE = {
    MP3: "audio/mpeg",
    AAC: "audio/aac",
    M4A: "audio/mp4",
    MP4: "video/mp4",
    MOV: "video/quicktime",
    WEBM: "video/webm",
    OGG: "audio/ogg",
    WAV: "audio/wav",
} as const;
export type MediaType = (typeof MEDIA_TYPE)[keyof typeof MEDIA_TYPE];

// The brands of an ISO base media file's `ftyp` box the page plays; an image brand (heic, avif) is none of them.
const FTYP_BRANDS: ReadonlyMap<string, MediaType> = new Map([
    ["M4A ", MEDIA_TYPE.M4A],
    ["M4B ", MEDIA_TYPE.M4A],
    ["M4P ", MEDIA_TYPE.M4A],
    ["qt  ", MEDIA_TYPE.MOV],
    ...[
        "isom",
        "iso2",
        "iso4",
        "iso5",
        "iso6",
        "mp41",
        "mp42",
        "avc1",
        "dash",
        "M4V ",
        "MSNV",
        "3gp4",
        "3gp5",
        "3gp6",
        "3gp7",
        "3g2a",
    ].map((brand): [string, MediaType] => [brand, MEDIA_TYPE.MP4]),
]);

/** How the page shows a file whose bytes it knows. */
export const FILE_DISPLAY = {
    PICTURE: "picture",
    AUDIO: "audio",
    VIDEO: "video",
} as const;
export type FileDisplay = (typeof FILE_DISPLAY)[keyof typeof FILE_DISPLAY];

/** The type its bytes show and the element that shows it. */
export interface Shown {
    display: FileDisplay;
    type: PictureType | MediaType;
}

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
    if (textAt(head, RIFF) && textAt(head, WEBP, RIFF_FORMAT_OFFSET)) return PICTURE_TYPE.WEBP;
    return null;
}

function isMpegFrame(head: Uint8Array): boolean {
    const second = head[1] as number;
    return (
        head[0] === FRAME_SYNC_FIRST_BYTE &&
        (second & FRAME_SYNC_SECOND_BYTE_MASK) === FRAME_SYNC_SECOND_BYTE_MASK &&
        ((second >> MPEG_VERSION_SHIFT) & TWO_BIT_MASK) !== RESERVED_MPEG_VERSION &&
        ((second >> MPEG_LAYER_SHIFT) & TWO_BIT_MASK) !== 0
    );
}

function isAdtsFrame(head: Uint8Array): boolean {
    return head[0] === FRAME_SYNC_FIRST_BYTE && ((head[1] as number) & ADTS_SECOND_BYTE_MASK) === ADTS_SECOND_BYTE;
}

/** The song or video type the first bytes show: MP3, AAC, M4A, MP4, MOV, WebM, Ogg or WAV; null when they show none. */
export function mediaTypeOf(head: Uint8Array): MediaType | null {
    if (textAt(head, ID3) || isMpegFrame(head)) return MEDIA_TYPE.MP3;
    if (isAdtsFrame(head)) return MEDIA_TYPE.AAC;
    if (textAt(head, FTYP, FTYP_BOX_TYPE_OFFSET)) {
        return FTYP_BRANDS.get(String.fromCharCode(...head.subarray(FTYP_BRAND_OFFSET, FTYP_BRAND_OFFSET + 4))) ?? null;
    }
    if (startsWith(head, EBML_SIGNATURE)) return MEDIA_TYPE.WEBM;
    if (textAt(head, OGG)) return MEDIA_TYPE.OGG;
    if (textAt(head, RIFF) && textAt(head, WAVE, RIFF_FORMAT_OFFSET)) return MEDIA_TYPE.WAV;
    return null;
}

/** How the first bytes say the file is shown, with the type its blob takes; null when they show none. */
export function shownOf(head: Uint8Array): Shown | null {
    const picture = pictureTypeOf(head);
    if (picture !== null) return { display: FILE_DISPLAY.PICTURE, type: picture };
    const media = mediaTypeOf(head);
    if (media === null) return null;
    return { display: media.startsWith("video/") ? FILE_DISPLAY.VIDEO : FILE_DISPLAY.AUDIO, type: media };
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
