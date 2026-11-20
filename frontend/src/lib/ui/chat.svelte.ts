// The chat panel's open state, unread count, draft and lines, rendered by the layout. A feature's lines, pinned
// bubble and card are feature views (`featureView`: a component bound to its props), rendered inside `display:
// contents` wrappers.

import { flushSync, type Component } from "svelte";
import type { Attachment } from "svelte/attachments";
import { LINE_REFUSAL, lineRefusal } from "#lib/call/line.ts";
import { type AgentLink, DELIVERY, type Delivery, lines } from "#lib/call/lines.ts";
import { playMessageSound } from "#lib/call/sounds.ts";
import { state } from "#lib/call/state.ts";
import { LINE_MAX_CHARACTERS } from "#lib/wire.ts";
import { words } from "#lib/words.ts";

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
    | { kind: "feature"; view: FeatureView };

export const chat: {
    isOpen: boolean;
    unread: number;
    draft: string;
    lines: ChatLine[];
    pinned: FeatureView | null;
    card: FeatureView | null;
    readers: readonly string[];
} = $state({ isOpen: false, unread: 0, draft: "", lines: [], pinned: null, card: null, readers: [] });

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

/** The text the chat shows for the agents that read along, or null when none does. */
export function readersNote(): string | null {
    return chat.readers.length === 0 ? null : words["chat.readersNote"]({ names: chat.readers.join(", ") });
}

/** The text under a line that did not reach everyone, or null when it did or still may. */
export function undeliveredNote(line: ChatLine): string | null {
    if (line.kind !== "self" || line.delivery !== DELIVERY.UNDELIVERED) return null;
    return words["chat.lineUndelivered"]({ names: line.missing.join(", ") });
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
        delivery: (id, delivery, missing) => {
            const line = chat.lines.find((each) => each.kind === "self" && each.id === id);
            if (line?.kind !== "self") return;
            line.delivery = delivery;
            line.missing = missing;
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
