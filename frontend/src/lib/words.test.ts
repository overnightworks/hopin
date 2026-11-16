import { describe, expect, it } from "vitest";
import { pageLanguage } from "./words.ts";

describe("pageLanguage", () => {
    it("speaks the tag of the document when the app offers it", () => {
        expect(pageLanguage("fr", ["de", "fr"])).toBe("fr");
    });

    it("speaks the source language when the app does not offer the tag", () => {
        expect(pageLanguage("fr", ["de"])).toBe("de");
    });

    it("speaks the source language when the document has no tag", () => {
        expect(pageLanguage("", ["de"])).toBe("de");
    });
});
