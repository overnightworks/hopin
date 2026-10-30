export const greeting: { name: string | null; isBusy: boolean } = $state({ name: null, isBusy: true });

let onStartRequested: (() => void) | null = null;

export function showGreeting(name: string | null) {
    if (name !== null) greeting.name = name;
    greeting.isBusy = false;
}

export function installStartRequested(callback: () => void) {
    if (onStartRequested) throw new Error("The start request is already installed");
    onStartRequested = callback;
}

export function requestStart() {
    if (!onStartRequested) throw new Error("The start request is not installed");
    onStartRequested();
}
