import { loadKitWords } from "overnightworks-design";
import { language } from "#lib/words.ts";

// The kit loads the page's language; the type check fails when the app offers a language the kit lacks.
export const load = async () => ({ kitWords: await loadKitWords(language) });
