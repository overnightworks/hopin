// Every legal row links these, in this order.
const CENTRAL_LEGAL_ORIGIN = "https://hallucinai.de";
const APP_LEGAL_ANCHOR = "#hopin";

function centralLegalAddress(path: string): string {
    return `${CENTRAL_LEGAL_ORIGIN}/${path}${APP_LEGAL_ANCHOR}`;
}

export const LEGAL_LINKS = [
    { label: "Impressum", href: centralLegalAddress("impressum") },
    { label: "Datenschutz", href: centralLegalAddress("datenschutz") },
    { label: "Nutzungsbedingungen", href: centralLegalAddress("nutzungsbedingungen") },
];
