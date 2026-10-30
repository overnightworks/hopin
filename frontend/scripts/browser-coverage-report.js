// Turns the raw V8 coverage the end-to-end suite collected from Chromium into an lcov report of frontend/src.
// Usage: node scripts/browser-coverage-report.js <raw coverage directory> <page build directory> <report directory>
import { existsSync } from "node:fs";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
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
    await report.add(await Promise.all(bundles.map(withBundleAndMap)));
}
await report.generate();
