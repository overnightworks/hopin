import { describe, expect, it } from "vitest";
import { isFetchedAtOnce, isWithinCap, PICTURE_TYPE, pictureTypeOf, safeFileName } from "#lib/call/file.ts";
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

describe("what is fetched at once", () => {
    it("is a picture announced as raster up to the limit, and nothing else", () => {
        expect(isFetchedAtOnce("image/png", PICTURE_AUTO_FETCH_BYTES)).toBe(true);
        expect(isFetchedAtOnce("image/png", PICTURE_AUTO_FETCH_BYTES + 1)).toBe(false);
        expect(isFetchedAtOnce("image/svg+xml", 10)).toBe(false);
        expect(isFetchedAtOnce("application/pdf", 10)).toBe(false);
    });
});

describe("the per-file cap", () => {
    it("holds a file of one byte up to the cap", () => {
        expect(isWithinCap(0)).toBe(false);
        expect(isWithinCap(1)).toBe(true);
        expect(isWithinCap(FILE_MAX_BYTES)).toBe(true);
        expect(isWithinCap(FILE_MAX_BYTES + 1)).toBe(false);
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
