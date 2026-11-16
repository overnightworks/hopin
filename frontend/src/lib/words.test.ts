import { afterEach, describe, expect, it, vi } from "vitest";
import { pageLanguage } from "./words.ts";
import { languages, sourceLanguage } from "./words/index.ts";

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

describe("language", () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.resetModules();
    });

    it("is the tag of the document when the app offers it", async () => {
        const offered = languages[languages.length - 1];
        vi.stubGlobal("document", { documentElement: { lang: offered } });
        vi.resetModules();
        const { language } = await import("./words.ts");
        expect(language).toBe(offered);
    });

    it("is the source language when no document exists", async () => {
        vi.resetModules();
        const { language } = await import("./words.ts");
        expect(language).toBe(sourceLanguage);
    });
});
