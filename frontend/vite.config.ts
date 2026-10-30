import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vitest/config";

export default defineConfig(({ mode }) => ({
    build: {
        rolldownOptions: {
            // FastAPI serves the wire vocabulary the game reads and the game module under /static/; the bundle does not own them.
            external: [/^\/static\//],
        },
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
            exclude: ["src/lib/legacy/**", "src/lib/wire.ts", "**/*.test.ts"],
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
            dynamicCompileOptions: ({ filename }) => (filename.includes("node_modules") ? undefined : { runes: true }),
        }),
    ],
}));
