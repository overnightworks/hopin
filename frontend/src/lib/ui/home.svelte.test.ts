// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

// The module holds its state and the installed callback, so each case loads a fresh copy.
async function freshHome() {
    vi.resetModules();
    return import("#lib/ui/home.svelte.ts");
}

describe("home greeting", () => {
    it("starts busy without a name", async () => {
        const { greeting } = await freshHome();

        expect(greeting).toEqual({ name: null, isBusy: true });
    });

    it("keeps the name and clears busy when a name is shown", async () => {
        const { greeting, showGreeting } = await freshHome();

        showGreeting("Ada");

        expect(greeting).toEqual({ name: "Ada", isBusy: false });
    });

    it("clears busy and keeps no name when the name is null", async () => {
        const { greeting, showGreeting } = await freshHome();

        showGreeting(null);

        expect(greeting).toEqual({ name: null, isBusy: false });
    });
});

describe("home start request", () => {
    it("calls the installed callback", async () => {
        const home = await freshHome();
        const onStartRequested = vi.fn();
        home.installStartRequested(onStartRequested);

        home.requestStart();

        expect(onStartRequested).toHaveBeenCalledOnce();
    });

    it("throws on a second install", async () => {
        const home = await freshHome();
        home.installStartRequested(vi.fn());

        expect(() => {
            home.installStartRequested(vi.fn());
        }).toThrow("already installed");
    });

    it("throws on a request before a callback is installed", async () => {
        const home = await freshHome();

        expect(() => {
            home.requestStart();
        }).toThrow("not installed");
    });
});
