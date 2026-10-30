import { afterEach, describe, expect, it, vi } from "vitest";
import { CALL_FEATURES_PATH } from "#lib/wire.ts";
import { loadFeatures } from "./features.ts";

const core = { marker: "core" };

function offer(response: Response | Error) {
    const fetchStub = vi.fn(() => (response instanceof Error ? Promise.reject(response) : Promise.resolve(response)));
    vi.stubGlobal("fetch", fetchStub);
    return fetchStub;
}

function offerNames(names: unknown) {
    return offer(Response.json({ features: names }));
}

function featureEntry(name: string, module: unknown) {
    return { [`/src/lib/features/${name}/index.js`]: () => Promise.resolve(module) };
}

afterEach(() => {
    vi.unstubAllGlobals();
});

describe("loadFeatures", () => {
    it("installs each offered feature with the core and resolves true", async () => {
        const fetchStub = offerNames(["alpha", "beta"]);
        const installed: [string, unknown][] = [];
        const bundled = {
            ...featureEntry("alpha", { install: (given: unknown) => installed.push(["alpha", given]) }),
            ...featureEntry("beta", { install: (given: unknown) => installed.push(["beta", given]) }),
            ...featureEntry("gamma", { install: () => expect.unreachable("gamma was not offered") }),
        };

        expect(await loadFeatures(bundled, core)).toBe(true);
        expect(fetchStub).toHaveBeenCalledWith(CALL_FEATURES_PATH);
        expect(installed).toEqual([
            ["alpha", core],
            ["beta", core],
        ]);
    });

    it("waits for an asynchronous install before it resolves", async () => {
        offerNames(["alpha"]);
        let finishInstall: () => void = () => undefined;
        let finished = false;
        const install = () =>
            new Promise<void>((resolve) => {
                finishInstall = () => {
                    finished = true;
                    resolve();
                };
            });
        let resolved = false;

        const loading = loadFeatures(featureEntry("alpha", { install }), core).then((loaded) => {
            resolved = true;
            return loaded;
        });
        await new Promise((resolve) => setTimeout(resolve, 0));
        expect(resolved).toBe(false);

        finishInstall();

        expect(await loading).toBe(true);
        expect(finished).toBe(true);
    });

    it("resolves true when the server offers no feature", async () => {
        offerNames([]);

        expect(await loadFeatures({}, core)).toBe(true);
    });

    it("matches a feature by its directory, not by the tail of another name", async () => {
        offerNames(["game"]);

        expect(await loadFeatures(featureEntry("minigame", { install: () => undefined }), core)).toBe(false);
    });

    it.each([
        ["a failed answer", () => offer(new Response("", { status: 401 }))],
        ["an unreachable server", () => offer(new Error("offline"))],
        ["an answer that is no JSON", () => offer(new Response("<html>"))],
        ["an answer that is no object", () => offer(Response.json("game"))],
        ["an answer that is null", () => offer(Response.json(null))],
        ["an answer without features", () => offer(Response.json({}))],
        ["features that are no array", () => offerNames("game")],
        ["a feature name that is no string", () => offerNames([7])],
    ])("resolves false for %s", async (_label, answer) => {
        answer();

        expect(await loadFeatures(featureEntry("game", { install: () => undefined }), core)).toBe(false);
    });

    it.each([
        ["a name with no bundled entry", {}],
        [
            "an entry whose import fails",
            { "/src/lib/features/game/index.js": () => Promise.reject(new Error("chunk")) },
        ],
        ["a module that is no object", featureEntry("game", "text")],
        ["a null module", featureEntry("game", null)],
        ["a module without install", featureEntry("game", {})],
        ["a module whose install is no function", featureEntry("game", { install: 1 })],
        ["an install that rejects", featureEntry("game", { install: () => Promise.reject(new Error("install")) })],
    ])("resolves false for %s", async (_label, bundled) => {
        offerNames(["game"]);

        expect(await loadFeatures(bundled, core)).toBe(false);
    });
});
