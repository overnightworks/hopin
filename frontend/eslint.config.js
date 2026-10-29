import eslint from "@eslint/js";
import prettier from "eslint-config-prettier";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import ts from "typescript-eslint";

export default ts.config(
    { linterOptions: { reportUnusedDisableDirectives: "error" } },
    eslint.configs.recommended,
    ...ts.configs.strictTypeChecked,
    ...svelte.configs.recommended,
    prettier,
    ...svelte.configs.prettier,
    {
        languageOptions: {
            globals: { ...globals.browser, ...globals.node },
            parserOptions: {
                projectService: { allowDefaultProject: ["*.js"] },
                extraFileExtensions: [".svelte"],
            },
        },
    },
    {
        files: ["**/*.svelte", "**/*.svelte.ts"],
        languageOptions: { parserOptions: { parser: ts.parser } },
    },
    {
        files: ["**/*.js"],
        extends: [ts.configs.disableTypeChecked],
    },
    {
        files: ["**/*.svelte"],
        rules: {
            "svelte/no-inline-styles": ["error", { allowTransitions: false }],
            "svelte/no-at-html-tags": "error",
            "no-restricted-syntax": [
                "error",
                { selector: "SvelteStyleElement", message: "Components carry classes only; styles live in hopin.css." },
            ],
        },
    },
    {
        rules: { "@typescript-eslint/no-explicit-any": "error" },
    },
    {
        // The page code under lib/legacy is reached only by the layout's dynamic import after mount, never statically.
        ignores: ["src/lib/legacy/**"],
        rules: {
            "no-restricted-imports": [
                "error",
                {
                    patterns: [
                        {
                            group: ["#lib/legacy/**", "**/legacy/**"],
                            message: "Legacy page code loads only through the layout's dynamic import after mount.",
                        },
                    ],
                },
            ],
        },
    },
    { ignores: ["build/", ".svelte-kit/", "coverage/", "../src/hopin/web/build/"] },
);
