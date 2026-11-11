import { describe, expect, it } from "vitest";
import { UNEXPECTED_ERROR_TEXT } from "#lib/texts.ts";

describe("UNEXPECTED_ERROR_TEXT", () => {
    it("is a non-empty sentence a person can read", () => {
        expect(UNEXPECTED_ERROR_TEXT.trim()).not.toBe("");
        expect(UNEXPECTED_ERROR_TEXT).toMatch(/[.!?]$/);
    });
});
