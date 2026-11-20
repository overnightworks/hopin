// The loader of the call's optional features. The page starts with or without the game: each feature is bundled
// under `frontend/src/lib/features/<module>/`, and `GET /api/call-features` (`web/call_features.py`) names the
// browser modules of every wired feature (`TransitionFeature.browser_modules`). The start-up module
// (`routes/(call)/start-up.ts`) hands the bundle's `import.meta.glob` of those entries to `loadFeatures`, which
// imports each named module and awaits its `install` before signaling connects, because signaling drops for good a
// frame no handler takes. The features load once, at the first call start, never at page load. A feature that fails
// to load, its stylesheet included, never stops the call: `loadFeatures` resolves to `false`, and the page starts
// without it and shows one generic toast.
//
// The import contract `call-core-knows-no-game` (`pyproject.toml`) keeps the call core from reaching the game,
// participation or Skat, and its eslint twin in `frontend/eslint.config.js` keeps the frontend's core code from
// importing any `features/` module.

import type { FeatureCore, FeatureModule } from "#lib/call/feature-port.ts";
import { CALL_FEATURES_PATH } from "#lib/wire.ts";

export type BundledFeatures = Readonly<Record<string, () => Promise<unknown>>>;

interface FeatureList {
    features: string[];
}

function isFeatureList(body: unknown): body is FeatureList {
    return (
        typeof body === "object" &&
        body !== null &&
        "features" in body &&
        Array.isArray(body.features) &&
        body.features.every((name) => typeof name === "string")
    );
}

async function featureNames(): Promise<string[]> {
    const response = await fetch(CALL_FEATURES_PATH);
    if (!response.ok) {
        throw new Error(`${CALL_FEATURES_PATH} answered ${String(response.status)}`);
    }
    const body: unknown = await response.json();
    if (!isFeatureList(body)) {
        throw new Error(`${CALL_FEATURES_PATH} answered no list of feature names`);
    }
    return body.features;
}

function isFeatureModule(module: unknown): module is FeatureModule {
    return typeof module === "object" && module !== null && "install" in module && typeof module.install === "function";
}

async function installFeature(name: string, bundled: BundledFeatures, core: FeatureCore<never>): Promise<void> {
    const entry = Object.entries(bundled).find(([path]) => path.includes(`/features/${name}/`));
    if (entry === undefined) {
        throw new Error(`The page bundles no feature ${name}`);
    }
    const module = await entry[1]();
    if (!isFeatureModule(module)) {
        throw new Error(`The feature ${name} has no install`);
    }
    await module.install(core);
}

/** Installs every feature the server offers; resolves to whether all are in. A failure never reaches the caller. */
export async function loadFeatures(bundled: BundledFeatures, core: FeatureCore<never>): Promise<boolean> {
    try {
        const names = await featureNames();
        await Promise.all(names.map((name) => installFeature(name, bundled, core)));
        return true;
    } catch {
        return false;
    }
}
