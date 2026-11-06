import { describe, expect, it } from "vitest";
import { LEGAL_LINKS } from "#lib/legal.ts";

describe("LEGAL_LINKS", () => {
    it("gives every link an absolute https address with a fragment and a distinct non-empty label", () => {
        for (const link of LEGAL_LINKS) {
            const address = new URL(link.href);
            expect(address.protocol).toBe("https:");
            expect(address.hash.length).toBeGreaterThan(1);
            expect(link.label.trim()).not.toBe("");
        }
        const labels = LEGAL_LINKS.map((link) => link.label);
        expect(new Set(labels).size).toBe(labels.length);
    });
});
