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
const gameMasterSenderTemplate = document.getElementById("gameMasterSenderTemplate");

let chatOpen = false;
let unreadMessages = 0;
let gameMasterTyping = null;

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
    // The game master's typing bubble stays last while it thinks.
    chatMessages.insertBefore(messageElement, gameMasterTyping);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function gameMasterBubble() {
    const messageElement = document.createElement("div");
    messageElement.className = "chat-msg other game-master";
    messageElement.append(gameMasterSenderTemplate.content.firstElementChild.cloneNode(true));
    return messageElement;
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

export function receiveGameMasterLine(text) {
    const messageElement = gameMasterBubble();
    messageElement.append(text);
    showMessage(messageElement);
    countUnread();
}

export function showGameMasterThinking(isThinking) {
    if (isThinking === (gameMasterTyping !== null)) return;
    if (isThinking) {
        gameMasterTyping = gameMasterBubble();
        gameMasterTyping.classList.add("typing");
        gameMasterTyping.setAttribute("role", "status");
        gameMasterTyping.setAttribute("aria-label", "Game master is thinking");
        const dots = document.createElement("p");
        dots.append(...Array.from({ length: 3 }, () => document.createElement("i")));
        gameMasterTyping.append(dots);
        chatMessages.appendChild(gameMasterTyping);
        chatMessages.scrollTop = chatMessages.scrollHeight;
    } else {
        gameMasterTyping.remove();
        gameMasterTyping = null;
    }
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
