import { playMessageSound } from "./sounds.js";
import { SIGNAL_TYPE, isConnected, on, send } from "./signaling.js";
import { state } from "./state.js";

const chatPanel = document.getElementById("chatPanel");
const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const chatSend = document.getElementById("chatSend");
const chatButton = document.getElementById("chatBtn");
const chatClose = document.getElementById("chatClose");
const chatBadge = document.getElementById("chatBadge");

let chatOpen = false;
let unreadMessages = 0;
let pinnedLast = null;
let card = null;

function appendMessage(text, senderId) {
    const isSelf = senderId === null;
    const messageElement = document.createElement("div");
    messageElement.className = `chat-msg ${isSelf ? "self" : "other"}`;
    if (!isSelf) {
        const senderElement = document.createElement("div");
        senderElement.className = "chat-sender";
        senderElement.textContent = state.namesByParticipant.get(senderId) ?? "";
        messageElement.appendChild(senderElement);
    }
    messageElement.append(text);
    showMessage(messageElement);
}

function showMessage(messageElement) {
    if (pinnedLast === null) chatMessages.append(messageElement);
    else pinnedLast.before(messageElement);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function countUnread() {
    if (!chatOpen) {
        unreadMessages++;
        chatBadge.textContent = unreadMessages;
        chatBadge.classList.add("visible");
        playMessageSound();
    }
}

function receiveMessage(text, senderId) {
    appendMessage(text, senderId);
    countUnread();
}

/** Show a line no person wrote, counted as unread like a person's. */
export function receiveLine(messageElement) {
    showMessage(messageElement);
    countUnread();
}

/** Keep `element` below every later line until another element, or null, takes its place. */
export function pinLast(element) {
    pinnedLast?.remove();
    pinnedLast = element;
    if (element === null) return;
    chatMessages.append(element);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

/** Show `element` between the chat's header and its lines until another element, or null, takes its place. */
export function showCard(element) {
    card?.remove();
    card = element;
    if (element !== null) chatMessages.before(element);
}

function sendMessage() {
    const text = chatInput.value.trim();
    if (!text || !isConnected()) return;
    send({ type: SIGNAL_TYPE.CHAT, message: text });
    appendMessage(text, null);
    chatInput.value = "";
}

function renderChatButton() {
    chatButton.classList.toggle("active", chatOpen);
    chatButton.setAttribute("aria-expanded", String(chatOpen));
}

function toggleChat() {
    chatOpen = !chatOpen;
    chatPanel.classList.toggle("visible", chatOpen);
    renderChatButton();
    if (chatOpen) {
        unreadMessages = 0;
        chatBadge.classList.remove("visible");
        chatInput.focus();
    }
}

function closeChat() {
    chatOpen = false;
    chatPanel.classList.remove("visible");
    renderChatButton();
}

export function registerChatHandlers() {
    on(SIGNAL_TYPE.CHAT, (data) => receiveMessage(data.message, data.from));
}

export function initChat() {
    chatButton.addEventListener("click", toggleChat);
    chatClose.addEventListener("click", closeChat);
    chatSend.addEventListener("click", sendMessage);
    chatInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") sendMessage();
    });
}
