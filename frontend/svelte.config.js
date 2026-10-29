import adapter from "@sveltejs/adapter-static";

/** @type {import('@sveltejs/kit').Config} */
const config = {
    kit: {
        adapter: adapter({
            pages: "../src/hopin/web/build",
            assets: "../src/hopin/web/build",
            fallback: "index.html",
        }),
    },
    vitePlugin: {
        dynamicCompileOptions: ({ filename }) => (filename.includes("node_modules") ? undefined : { runes: true }),
    },
};

export default config;
