import { loadKitWords } from "overnightworks-design";
import { sourceLanguage } from "#lib/words/index.ts";

// `sourceLanguage` is the app's `Language`; the type check fails when the app offers a language the kit lacks.
export const load = async () => ({ kitWords: await loadKitWords(sourceLanguage) });
