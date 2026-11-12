// Generated from the de catalog by design-catalogs. Do not edit.

export const language = "de";
// prettier-ignore
export type Words = {
    readonly "call.unexpectedError": string;
    readonly "end.accountButton": string;
    readonly "end.retryButton": string;
    readonly "end.homeButton": string;
    readonly "end.rejoinButton": string;
    readonly "home.accountButton": string;
    readonly "home.greetingUnnamed": string;
    readonly "home.greetingNamed": (gaps: { readonly name: string }) => string;
    readonly "home.invitation": string;
    readonly "home.startButton": string;
    readonly "prejoin.accountButton": string;
    readonly "prejoin.backButton": string;
    readonly "prejoin.muteButton": string;
    readonly "prejoin.cameraOffButton": string;
};
// prettier-ignore
export const words: Words = {
    "call.unexpectedError": "Etwas ist schiefgegangen. Bitte versuch es noch einmal.",
    "end.accountButton": "Konto",
    "end.retryButton": "Erneut versuchen",
    "end.homeButton": "Zurück zum Start",
    "end.rejoinButton": "Wieder beitreten",
    "home.accountButton": "Konto",
    "home.greetingUnnamed": "Hallo",
    "home.greetingNamed": (gaps: { readonly name: string }) => "Hallo, " + gaps.name,
    "home.invitation": "Starte einen Anruf und schick den Link dann an die Leute, die du dabeihaben möchtest.",
    "home.startButton": "Anruf starten",
    "prejoin.accountButton": "Konto",
    "prejoin.backButton": "Zurück",
    "prejoin.muteButton": "Stummschalten",
    "prejoin.cameraOffButton": "Kamera ausschalten",
};
export type Key = keyof Words;
