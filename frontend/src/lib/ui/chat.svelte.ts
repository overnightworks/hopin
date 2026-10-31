import { flushSync, type Component } from "svelte";
import type { Attachment } from "svelte/attachments";
import { isConnected, on, send, type ServerFrame } from "#lib/call/signaling.ts";
import { playMessageSound } from "#lib/call/sounds.ts";
import { state } from "#lib/call/state.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";

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
    | { kind: "self"; text: string }
    | { kind: "other"; text: string; sender: string }
    | { kind: "feature"; view: FeatureView };

export const chat: {
    isOpen: boolean;
    unread: number;
    draft: string;
    lines: ChatLine[];
    pinned: FeatureView | null;
    card: FeatureView | null;
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
