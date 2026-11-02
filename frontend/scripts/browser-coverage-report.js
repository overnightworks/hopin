// Turns the raw V8 coverage the end-to-end suite collected from Chromium into an lcov report of frontend/src.
// Usage: node scripts/browser-coverage-report.js <raw coverage directory> <page build directory> <report directory>
import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { decode } from "@jridgewell/sourcemap-codec";
import { CoverageReport } from "monocart-coverage-reports";

const [rawDirectory, buildDirectory, reportDirectory] = process.argv.slice(2);
const bundlePath = (entry) => path.join(buildDirectory, new URL(entry.url).pathname);
// Vite's own preload helper is a bundle with no map and no source of ours.
const hasMap = (entry) => entry.url.includes("/_app/") && existsSync(`${bundlePath(entry)}.map`);

const report = new CoverageReport({
    name: "Browser coverage",
    outputDir: reportDirectory,
    reports: ["lcovonly", "console-summary"],
    outputFilter: { "frontend/src/**": true, "**/*": false },
});

// Chromium reports a script's coverage without its text, and the bundles carry no pointer to their hidden maps.
async function withBundleAndMap(entry) {
    const bundle = bundlePath(entry);
    return {
        ...entry,
        source: await readFile(bundle, "utf8"),
        sourceMap: JSON.parse(await readFile(`${bundle}.map`, "utf8")),
    };
}

for (const file of await readdir(rawDirectory)) {
    const { result } = JSON.parse(await readFile(path.join(rawDirectory, file), "utf8"));
    const bundles = result.filter(hasMap);
    if (bundles.length > 0) {
        await report.add(await Promise.all(bundles.map(withBundleAndMap)));
    }
}
await report.generate();

// Sources that run outside a browser: client-side rendering is off for the sign-in group, so they run at prerender only.
// Any other missing source gets a test that loads it, or is deleted.
const PRERENDER_ONLY = [
    "frontend/src/lib/ui/SignIn.svelte",
    "frontend/src/routes/(sign-in)/login-failed/+page.svelte",
    "frontend/src/routes/(sign-in)/login/+page.svelte",
    "frontend/src/routes/(sign-in)/no-access/+page.svelte",
];
const repositoryRoot = path.resolve("..");

// Every frontend/src source that emits code in the page build, with the segments the build maps to it.
async function sourcesWithCode() {
    const segmentCounts = new Map();
    const maps = (await readdir(path.join(buildDirectory, "_app"), { recursive: true })).filter((file) =>
        file.endsWith(".js.map"),
    );
    for (const file of maps) {
        const mapPath = path.join(buildDirectory, "_app", file);
        const { sources, mappings } = JSON.parse(await readFile(mapPath, "utf8"));
        for (const segment of decode(mappings).flat()) {
            if (segment.length < 4) continue;
            const name = path.relative(repositoryRoot, path.resolve(path.dirname(mapPath), sources[segment[1]]));
            segmentCounts.set(name, (segmentCounts.get(name) ?? 0) + 1);
        }
    }
    return new Set([...segmentCounts.keys()].filter((name) => name.startsWith("frontend/src/")));
}

const reported = new Set(
    (await readFile(path.join(reportDirectory, "lcov.info"), "utf8"))
        .split("\n")
        .filter((line) => line.startsWith("SF:"))
        .map((line) => line.slice("SF:".length)),
);
const expected = await sourcesWithCode();
const orphans = PRERENDER_ONLY.filter((name) => !expected.has(name));
const missing = [...expected].filter((name) => !reported.has(name) && !PRERENDER_ONLY.includes(name)).sort();
console.log(`Unmeasured, prerender only (${PRERENDER_ONLY.length}):\n  ${PRERENDER_ONLY.join("\n  ")}`);
for (const [problem, names] of [
    ["Exempt sources that emit no code any more", orphans],
    ["Sources with code and no browser coverage record", missing],
]) {
    if (names.length > 0) {
        console.error(`${problem}:\n  ${names.join("\n  ")}`);
        process.exitCode = 1;
    }
}
