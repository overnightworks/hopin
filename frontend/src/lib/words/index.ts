// Generated from the catalogs by design-catalogs. Do not edit.

import type { Words } from "./de";

export const languages = ["de"] as const;
export type Language = (typeof languages)[number];
export const sourceLanguage: Language = "de";
// prettier-ignore
const modules: Record<Language, () => Promise<{ readonly words: Words }>> = {
    "de": () => import("./de"),
};
export const loadWords = async (language: Language): Promise<Words> => (await modules[language]()).words;
