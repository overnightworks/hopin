import { languages } from "#lib/words/index.ts";
import type { EntryGenerator } from "./login/$types";

// The build prerenders each sign-in page once per language, and the server answers with the file of the language it resolves.
export const entries: EntryGenerator = () => languages.map((language) => ({ language }));
