import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vitest/config";

export default defineConfig({
    build: {
        rolldownOptions: {
            // FastAPI serves the wire vocabulary the game reads and the game module under /static/; the bundle does not own them.
            external: [/^\/static\//],
        },
    },
    test: { environment: "node", include: ["src/**/*.test.ts"] },
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
});
