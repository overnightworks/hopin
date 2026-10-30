import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => ({
    build: {
        // The maps feed the browser coverage report; the bundle carries no pointer to them and the image ships none.
        sourcemap: "hidden",
    },
    // Svelte's mount needs its browser build under Vitest; the page build resolves as before.
    resolve: mode === "test" ? { conditions: ["browser"] } : undefined,
    test: {
        environment: "node",
        include: ["src/**/*.test.ts"],
        coverage: {
            provider: "v8",
            include: ["src/lib/**/*.ts"],
            // The legacy page code is judged by the end-to-end suite until it is ported; wire.ts is generated.
            exclude: ["src/lib/legacy/**", "src/lib/**/wire.ts", "**/*.test.ts"],
            reporter: [["text", { skipFull: false }], "lcov"],
            thresholds: { branches: 100, lines: 100, functions: 100, statements: 100 },
        },
    },
    plugins: [
        sveltekit({
            adapter: adapter({
                pages: "../src/hopin/web/build",
                assets: "../src/hopin/web/build",
                fallback: "index.html",
            }),
            // Absolute asset paths: the sign-in pages are served at /login and /auth/callback, where kit's relative default would resolve wrongly.
            paths: { relative: false },
            // The crawler would follow the card's /auth/start link and fail the build on a route that is not a page.
            prerender: { crawl: false },
            dynamicCompileOptions: ({ filename }) => (filename.includes("node_modules") ? undefined : { runes: true }),
        }),
    ],
}));
