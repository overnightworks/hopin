import { flushSync } from "svelte";
import type { Attachment } from "svelte/attachments";
import { isConnected, on, send, type ServerFrame } from "#lib/call/signaling.ts";
import { playMessageSound } from "#lib/call/sounds.ts";
import { state } from "#lib/call/state.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";

export type ChatLine =
    | { kind: "self"; text: string }
    | { kind: "other"; text: string; sender: string }
    | { kind: "element"; element: HTMLElement };

export const chat: {
    isOpen: boolean;
    unread: number;
    draft: string;
    lines: ChatLine[];
    pinned: HTMLElement | null;
    card: HTMLElement | null;
} = $state({ isOpen: false, unread: 0, draft: "", lines: [], pinned: null, card: null });

let listElement: HTMLElement | null = null;
let inputElement: HTMLElement | null = null;

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
export function receiveLine(element: HTMLElement) {
    addLine({ kind: "element", element });
    countUnread();
}

/** Keep `element` below every later line until another element, or null, takes its place. */
export function pinLast(element: HTMLElement | null) {
    chat.pinned = element;
    if (element !== null) scrollToNewest();
}

/** Show `element` between the chat's header and its lines until another element, or null, takes its place. */
export function showCard(element: HTMLElement | null) {
    chat.card = element;
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

export function sendChatMessage() {
    const message = chat.draft.trim();
    if (!message || !isConnected()) return;
    send({ type: SIGNAL_TYPE.CHAT, message });
    addLine({ kind: "self", text: message });
    chat.draft = "";
}

export function sendOnEnter(event: KeyboardEvent) {
    if (event.key === "Enter") sendChatMessage();
}

function isChatFrame(data: ServerFrame): data is ServerFrame & { message: string; from: string } {
    return typeof data.message === "string" && typeof data.from === "string";
}

export function registerChatHandlers() {
    on(SIGNAL_TYPE.CHAT, (data) => {
        if (!isChatFrame(data)) return;
        addLine({ kind: "other", text: data.message, sender: state.namesByParticipant.get(data.from) ?? "" });
        countUnread();
    });
}

/** Append `element` itself, never a clone: the game keeps changing the card after handing it over. */
export function adopt(element: HTMLElement): Attachment<HTMLElement> {
    return (wrapper) => {
        wrapper.append(element);
        return () => {
            element.remove();
        };
    };
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
