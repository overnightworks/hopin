import { describe, expect, it } from "vitest";
import {
    isFetchedAtOnce,
    fileRefusal,
    FILE_DISPLAY,
    FILE_REFUSAL,
    MEDIA_TYPE,
    mediaTypeOf,
    PICTURE_TYPE,
    pictureTypeOf,
    safeFileName,
    shownOf,
} from "#lib/call/file.ts";
import { FILE_MAX_BYTES, FILE_NAME_MAX_CHARACTERS, PICTURE_AUTO_FETCH_BYTES } from "#lib/wire.ts";

function bytes(...values: (number | string)[]): Uint8Array {
    return Uint8Array.from(
        values.flatMap((value) => (typeof value === "number" ? [value] : Array.from(value, (c) => c.charCodeAt(0)))),
    );
}

describe("the raster check", () => {
    it.each([
        ["PNG", bytes(0x89, "PNG", 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13), PICTURE_TYPE.PNG],
        ["JPEG", bytes(0xff, 0xd8, 0xff, 0xe0, 0, 16, "JFIF"), PICTURE_TYPE.JPEG],
        ["GIF87a", bytes("GIF87a", 1, 0, 1, 0), PICTURE_TYPE.GIF],
        ["GIF89a", bytes("GIF89a", 1, 0, 1, 0), PICTURE_TYPE.GIF],
        ["WebP", bytes("RIFF", 1, 2, 3, 4, "WEBPVP8 "), PICTURE_TYPE.WEBP],
    ])("takes the first bytes of a %s for a picture of its type", (_name, head, type) => {
        expect(pictureTypeOf(head)).toBe(type);
    });

    it.each([
        ["SVG", bytes('<svg xmlns="http://www.w3.org/2000/svg"/>')],
        ["SVG with a prolog", bytes('<?xml version="1.0"?><svg/>')],
        ["HTML", bytes("<!doctype html>")],
        ["a RIFF container that is no WebP", bytes("RIFF", 1, 2, 3, 4, "WAVEfmt ")],
        ["a PNG cut inside its signature", bytes(0x89, "PNG")],
        ["nothing", bytes()],
    ])("takes the first bytes of %s for no picture", (_name, head) => {
        expect(pictureTypeOf(head)).toBeNull();
    });
});

describe("the container check", () => {
    it.each([
        ["MP3 with an ID3 tag", bytes("ID3", 4, 0, 0, 0, 0, 0, 0, 0, 0), MEDIA_TYPE.MP3],
        ["MP3 from its frame sync", bytes(0xff, 0xfb, 0x90, 0x64, 0, 0, 0, 0), MEDIA_TYPE.MP3],
        ["AAC in ADTS", bytes(0xff, 0xf1, 0x50, 0x80, 0, 0, 0, 0), MEDIA_TYPE.AAC],
        ["M4A", bytes(0, 0, 0, 0x20, "ftypM4A ", 0, 0, 0, 0), MEDIA_TYPE.M4A],
        ["MP4", bytes(0, 0, 0, 0x18, "ftypisom", 0, 0, 2, 0), MEDIA_TYPE.MP4],
        ["MOV", bytes(0, 0, 0, 0x14, "ftypqt  ", 0, 0, 0, 0), MEDIA_TYPE.MOV],
        ["WebM", bytes(0x1a, 0x45, 0xdf, 0xa3, 0x9f, 0x42, 0x86, 0x81), MEDIA_TYPE.WEBM],
        ["Ogg", bytes("OggS", 0, 2, 0, 0, 0, 0, 0, 0), MEDIA_TYPE.OGG],
        ["WAV", bytes("RIFF", 1, 2, 3, 4, "WAVEfmt "), MEDIA_TYPE.WAV],
    ])("takes the first bytes of %s for a file of its type", (_name, head, type) => {
        expect(mediaTypeOf(head)).toBe(type);
    });

    it.each([
        ["an HEIC picture in an ISO base media box", bytes(0, 0, 0, 0x18, "ftypheic", 0, 0, 0, 0)],
        ["a frame sync of the reserved MPEG version", bytes(0xff, 0xeb, 0x90, 0x64)],
        ["a JPEG", bytes(0xff, 0xd8, 0xff, 0xe0, 0, 16, "JFIF")],
        ["a WebP", bytes("RIFF", 1, 2, 3, 4, "WEBPVP8 ")],
        ["HTML", bytes("<!doctype html>")],
        ["a box cut inside its brand", bytes(0, 0, 0, 0x18, "ftypis")],
        ["nothing", bytes()],
    ])("takes the first bytes of %s for no song or video", (_name, head) => {
        expect(mediaTypeOf(head)).toBeNull();
    });

    it("shows a picture as a picture, a video as a video and a song as audio, and anything else as nothing", () => {
        expect([
            shownOf(bytes(0x89, "PNG", 0x0d, 0x0a, 0x1a, 0x0a))?.display,
            shownOf(bytes(0, 0, 0, 0x18, "ftypisom"))?.display,
            shownOf(bytes("ID3", 4, 0, 0))?.display,
            shownOf(bytes("%PDF-1.7")),
        ]).toEqual([FILE_DISPLAY.PICTURE, FILE_DISPLAY.VIDEO, FILE_DISPLAY.AUDIO, null]);
    });
});

describe("what is fetched at once", () => {
    it("is a picture announced as raster up to the limit, and nothing else", () => {
        expect(isFetchedAtOnce("image/png", PICTURE_AUTO_FETCH_BYTES)).toBe(true);
        expect(isFetchedAtOnce("image/png", PICTURE_AUTO_FETCH_BYTES + 1)).toBe(false);
        expect(isFetchedAtOnce("image/svg+xml", 10)).toBe(false);
        expect(isFetchedAtOnce("application/pdf", 10)).toBe(false);
    });
});

describe("the per-file cap", () => {
    it("refuses an empty file and one over the cap, and nothing between", () => {
        expect(fileRefusal(0)).toBe(FILE_REFUSAL.EMPTY);
        expect(fileRefusal(1)).toBeNull();
        expect(fileRefusal(FILE_MAX_BYTES)).toBeNull();
        expect(fileRefusal(FILE_MAX_BYTES + 1)).toBe(FILE_REFUSAL.TOO_LARGE);
    });
});

describe("a sanitized file name", () => {
    it.each([
        ["holiday.png", "holiday.png"],
        ["../../etc/passwd", "....etcpasswd"],
        ["C:\\Users\\ada\\cv.pdf", "CUsersadacv.pdf"],
        ["cv\u202Efdp.exe", "cvfdp.exe"],
        ["tab\tand\nline.txt", "tabandline.txt"],
        ["  spaced  .txt  ", "spaced  .txt"],
        ["..", "datei"],
        ["", "datei"],
        ["\u202E/\\", "datei"],
    ])("turns %j into %j", (name, expected) => {
        expect(safeFileName(name)).toBe(expected);
    });

    it("keeps at most the limit's code points and never splits an astral character", () => {
        const name = safeFileName("\u{1F600}".repeat(FILE_NAME_MAX_CHARACTERS + 5));
        expect(Array.from(name)).toHaveLength(FILE_NAME_MAX_CHARACTERS);
    });
});
