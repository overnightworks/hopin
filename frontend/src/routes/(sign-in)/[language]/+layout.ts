import { loadKitWords } from "overnightworks-design";
import { languages } from "#lib/words/index.ts";
import { pageLanguage } from "#lib/words.ts";
import type { LayoutLoad } from "./$types";

export const load: LayoutLoad = async ({ params }) => ({
    kitWords: await loadKitWords(pageLanguage(params.language, languages)),
});
