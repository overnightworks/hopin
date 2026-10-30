import eslint from "@eslint/js";
import prettier from "eslint-config-prettier";
import svelte from "eslint-plugin-svelte";
import globals from "globals";
import ts from "typescript-eslint";

const LEGACY_IMPORTS = {
    group: ["#lib/legacy/**", "**/legacy/**"],
    message: "Legacy page code loads only through the layout's dynamic import after mount.",
};
const GAME_IMPORTS = {
    group: ["#lib/features/game/**", "**/features/game/**"],
    message: "The game is removable: the call's code never imports it; the page activates it by name.",
};

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
        // The call's code is bundled without the game and without the legacy page code, which load only dynamically.
        ignores: ["src/lib/legacy/**", "src/lib/features/game/**"],
        rules: { "no-restricted-imports": ["error", { patterns: [LEGACY_IMPORTS, GAME_IMPORTS] }] },
    },
    {
        // The page code under lib/legacy is reached only by the layout's dynamic import after mount, never statically.
        files: ["src/lib/features/game/**"],
        rules: { "no-restricted-imports": ["error", { patterns: [LEGACY_IMPORTS] }] },
    },
    {
        // The call-core-knows-no-game twin: the game is removable, so only its own directory reaches it.
        files: ["src/lib/legacy/**"],
        rules: { "no-restricted-imports": ["error", { patterns: [GAME_IMPORTS] }] },
    },
    { ignores: ["build/", ".svelte-kit/", "coverage/", "../src/hopin/web/build/"] },
);
