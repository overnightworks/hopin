import { playMessageSound } from "./sounds.js";
import { SIGNAL_TYPE, isConnected, on, send } from "./signaling.js";

const chatPanel = document.getElementById("chatPanel");
const chatMessages = document.getElementById("chatMessages");
const chatInput = document.getElementById("chatInput");
const chatSend = document.getElementById("chatSend");
const chatButton = document.getElementById("chatBtn");
const chatClose = document.getElementById("chatClose");
const chatBadge = document.getElementById("chatBadge");

let chatOpen = false;
let unreadMessages = 0;

function appendMessage(text, senderId) {
    const isSelf = senderId === null;
    const messageElement = document.createElement("div");
    messageElement.className = `chat-msg ${isSelf ? "self" : "other"}`;
    if (!isSelf) {
        const senderElement = document.createElement("div");
        senderElement.className = "chat-sender";
        senderElement.textContent = `Peer ${senderId.slice(0, 6)}`;
        messageElement.appendChild(senderElement);
    }
    messageElement.append(text);
    chatMessages.appendChild(messageElement);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function receiveMessage(text, senderId) {
    appendMessage(text, senderId);
    if (!chatOpen) {
        unreadMessages++;
        chatBadge.textContent = unreadMessages;
        chatBadge.classList.add("visible");
        playMessageSound();
    }
}

function sendMessage() {
    const text = chatInput.value.trim();
    if (!text || !isConnected()) return;
    send({ type: SIGNAL_TYPE.CHAT, message: text });
    appendMessage(text, null);
    chatInput.value = "";
}

function toggleChat() {
    chatOpen = !chatOpen;
    chatPanel.classList.toggle("visible", chatOpen);
    chatButton.className = chatOpen ? "btn-active" : "btn-secondary";
    if (chatOpen) {
        unreadMessages = 0;
        chatBadge.classList.remove("visible");
        chatInput.focus();
    }
}

function closeChat() {
    chatOpen = false;
    chatPanel.classList.remove("visible");
    chatButton.className = "btn-secondary";
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
