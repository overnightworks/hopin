import adapter from "@sveltejs/adapter-static";
import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

export default defineConfig({
    build: {
        rolldownOptions: {
            // FastAPI serves the legacy page code under /static/ until it moves into the bundle.
            external: [/^\/static\//],
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
});
