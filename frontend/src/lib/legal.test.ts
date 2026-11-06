import { describe, expect, it } from "vitest";
import { LEGAL_LINKS } from "#lib/legal.ts";

describe("LEGAL_LINKS", () => {
    it("lists Impressum, Datenschutz and Nutzungsbedingungen in this order, each on the central site at HopIn's anchor", () => {
        expect(LEGAL_LINKS.map((link) => link.label)).toEqual(["Impressum", "Datenschutz", "Nutzungsbedingungen"]);
        for (const link of LEGAL_LINKS) {
            const address = new URL(link.href);
            expect(address.hash).toBe("#hopin");
            expect(address.pathname.length).toBeGreaterThan(1);
        }
    });
});
