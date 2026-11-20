// The chat panel's open state, unread count, draft, posted-file problem and lines, rendered by the layout. A feature's lines, pinned
// bubble and card are feature views (`featureView`: a component bound to its props), rendered inside `display:
// contents` wrappers.

import { flushSync, type Component } from "svelte";
import type { Attachment } from "svelte/attachments";
import { LINE_REFUSAL, lineRefusal } from "#lib/call/line.ts";
import { FILE_PROBLEM, FILE_STATE, files, type FileView, POST_REFUSAL, type PostRefusal } from "#lib/call/files.ts";
import { type AgentLink, DELIVERY, type Delivery, lines } from "#lib/call/lines.ts";
import { playMessageSound } from "#lib/call/sounds.ts";
import { state } from "#lib/call/state.ts";
import { FILE_MAX_BYTES, FILE_MAX_RECEIVED_BYTES, LINE_MAX_CHARACTERS } from "#lib/wire.ts";
import { language, words } from "#lib/words.ts";

/** A feature's component with its props bound; core renders it and never looks inside. */
export type FeatureView = Component;

/** Binds `component` to `props`, so the pair is type-checked here and the layout renders it with no props. */
export function featureView<Props extends Record<string, unknown>>(
    component: Component<Props>,
    props: Props,
): FeatureView {
    return (internals) => component(internals, props);
}

export type ChatLine =
    | { kind: "self"; id: number; text: string; delivery: Delivery; missing: readonly string[] }
    | { kind: "other"; text: string; sender: string }
    | { kind: "file"; file: FileView }
    | { kind: "feature"; view: FeatureView };

export const chat: {
    isOpen: boolean;
    unread: number;
    draft: string;
    /** The file the composer did not post, and why. */
    unposted: { name: string; refusal: PostRefusal } | null;
    lines: ChatLine[];
    pinned: FeatureView | null;
    card: FeatureView | null;
    readers: readonly string[];
} = $state({
    isOpen: false,
    unread: 0,
    draft: "",
    unposted: null,
    lines: [],
    pinned: null,
    card: null,
    readers: [],
});

const BYTES_PER_MEGABYTE = 1024 * 1024;
const BYTES_PER_KILOBYTE = 1024;
const sizeFormat = new Intl.NumberFormat(language, { maximumFractionDigits: 1 });

let listElement: HTMLElement | null = null;
let inputElement: HTMLElement | null = null;
let fileInputElement: HTMLInputElement | null = null;

// The scroll and the focus need the new node and the visible panel, so the write is flushed first.
function scrollToNewest() {
    flushSync();
    if (listElement !== null) listElement.scrollTop = listElement.scrollHeight;
}

function addLine(line: ChatLine) {
    chat.lines.push(line);
    scrollToNewest();
}

function countUnread() {
    if (chat.isOpen) return;
    chat.unread++;
    playMessageSound();
}

/** Show a line no person wrote, counted as unread like a person's. */
export function receiveLine(view: FeatureView) {
    addLine({ kind: "feature", view });
    countUnread();
}

/** Keep `view` below every later line until another view, or null, takes its place. */
export function pinLast(view: FeatureView | null) {
    chat.pinned = view;
    if (view !== null) scrollToNewest();
}

/** Show `view` between the chat's header and its lines until another view, or null, takes its place. */
export function showCard(view: FeatureView | null) {
    chat.card = view;
    if (view !== null) scrollToNewest();
}

export function toggleChat() {
    chat.isOpen = !chat.isOpen;
    if (!chat.isOpen) return;
    chat.unread = 0;
    flushSync();
    inputElement?.focus();
}

export function closeChat() {
    chat.isOpen = false;
}

/** Why the draft is not sent, in the words the composer shows, or null when it is no problem. */
export function draftProblem(): string | null {
    switch (lineRefusal(chat.draft)) {
        case LINE_REFUSAL.TOO_LONG:
            return words["chat.lineTooLong"]({ max: String(LINE_MAX_CHARACTERS) });
        case LINE_REFUSAL.REFUSED_CHARACTER:
            return words["chat.lineRefusedCharacter"];
        default:
            return null;
    }
}

function megabytes(bytes: number): string {
    return String(bytes / BYTES_PER_MEGABYTE);
}

/** Why the file the composer was given is not posted, in the words the composer shows, or null when it was. */
export function unpostedNote(): string | null {
    if (chat.unposted === null) return null;
    const { name, refusal } = chat.unposted;
    switch (refusal) {
        case POST_REFUSAL.EMPTY:
            return words["chat.fileEmpty"]({ name });
        case POST_REFUSAL.TOO_LARGE:
            return words["chat.fileTooLarge"]({ name, max: megabytes(FILE_MAX_BYTES) });
        case POST_REFUSAL.BUSY:
            return words["chat.fileBusy"];
    }
}

/** The size of a file as the chat shows it. */
export function fileSize(bytes: number): string {
    if (bytes < BYTES_PER_MEGABYTE) {
        return words["chat.fileSizeKilobytes"]({ size: sizeFormat.format(bytes / BYTES_PER_KILOBYTE) });
    }
    return words["chat.fileSizeMegabytes"]({ size: sizeFormat.format(bytes / BYTES_PER_MEGABYTE) });
}

/** The text under a file about where it stands, or null when there is nothing to say. */
export function fileNote(file: FileView): string | null {
    if (file.problem === FILE_PROBLEM.BUSY) return words["chat.fileBusyReceiving"]({ name: file.sender });
    if (file.problem === FILE_PROBLEM.TOTAL) {
        return words["chat.fileTotalReceived"]({ max: megabytes(FILE_MAX_RECEIVED_BYTES) });
    }
    switch (file.state) {
        case FILE_STATE.LOADING:
            return words["chat.fileLoading"];
        case FILE_STATE.INCOMPLETE:
            return words["chat.fileIncomplete"];
        case FILE_STATE.REFUSED:
            return words["chat.fileRefused"]({ max: megabytes(FILE_MAX_BYTES) });
        default:
            return null;
    }
}

/** The text the chat shows for the agents that read along, or null when none does. */
export function readersNote(): string | null {
    return chat.readers.length === 0 ? null : words["chat.readersNote"]({ names: chat.readers.join(", ") });
}

function sentBy(line: ChatLine): { delivery: Delivery; missing: readonly string[] } | null {
    if (line.kind === "self") return line;
    return line.kind === "file" && line.file.own ? line.file : null;
}

/** The text under a line that did not reach everyone, or null when it did or still may. */
export function undeliveredNote(line: ChatLine): string | null {
    const sent = sentBy(line);
    return sent?.delivery === DELIVERY.UNDELIVERED
        ? words["chat.lineUndelivered"]({ names: sent.missing.join(", ") })
        : null;
}

/** Whether the page's own line or file still waits for someone's receipt. */
export function isPending(line: ChatLine): boolean {
    return sentBy(line)?.delivery === DELIVERY.PENDING;
}

/** Post the file the person picked in the composer. */
export function postFile(file: File) {
    const refusal = files.post(file);
    chat.unposted = refusal === null ? null : { name: file.name, refusal };
}

/** Open the device's file picker. */
export function pickFile() {
    fileInputElement?.click();
}

/** The person picked a file in the picker: post it, and let the picker offer the same file again. */
export function filePicked(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const [file] = input.files as FileList;
    if (file !== undefined) postFile(file);
    input.value = "";
}

/** Fetch a file that waits for a tap. */
export function loadFile(key: string) {
    files.fetch(key);
}

/** The browser could not decode the song or video the chat plays: offer it as a download. */
export function mediaUndecodable(file: FileView) {
    files.undecodable(file.key);
}

/** Save a received file under its name: the browser offers the download of the blob and never opens it. */
export function downloadFile(file: FileView) {
    if (file.url === null) return;
    const link = document.createElement("a");
    link.href = file.url;
    link.download = file.name;
    link.click();
}

/** Free every blob URL and held file: at leave and at the call's end. */
export function releaseChatFiles() {
    files.release();
}

export function sendChatMessage() {
    const sent = lines.send(chat.draft);
    if (sent === null) return;
    addLine({ kind: "self", ...sent });
    chat.draft = "";
}

/** Say which agents read the chat along, by name, and how a line reaches them; none named, when the last left. */
export function setLineReaders(link: AgentLink) {
    lines.linkAgents(link);
    chat.readers = [...link.names];
}

export function sendOnEnter(event: KeyboardEvent) {
    if (event.key === "Enter") sendChatMessage();
}

export function registerChatHandlers() {
    lines.listen({
        received: (participantId, text) => {
            addLine({ kind: "other", text, sender: state.namesByParticipant.get(participantId) ?? "" });
            countUnread();
        },
        announced: (participantId, id, announcement) => {
            files.announced(participantId, id, announcement);
        },
        delivery: (id, delivery, missing) => {
            const line = chat.lines.find((each) => each.kind === "self" && each.id === id);
            if (line?.kind === "self") {
                line.delivery = delivery;
                line.missing = missing;
            }
            files.delivered(id, delivery, missing);
        },
    });
    files.listen({
        shown: (file) => {
            addLine({ kind: "file", file });
            if (!file.own) countUnread();
        },
        changed: (file) => {
            const line = chat.lines.find((each) => each.kind === "file" && each.file.key === file.key);
            Object.assign((line as { file: FileView }).file, file);
        },
    });
}

export const chatList: Attachment<HTMLElement> = (element) => {
    listElement = element;
    return () => {
        listElement = null;
    };
};

export const chatInput: Attachment<HTMLElement> = (element) => {
    inputElement = element;
    return () => {
        inputElement = null;
    };
};

export const chatFileInput: Attachment<HTMLInputElement> = (element) => {
    fileInputElement = element;
    return () => {
        fileInputElement = null;
    };
};
