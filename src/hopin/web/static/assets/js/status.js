const statusElement = document.getElementById("status");
const lockIndicator = document.getElementById("lockIndicator");
const participantCountElement = document.getElementById("participantCount");

export function setStatus(text, type) {
    statusElement.textContent = text;
    statusElement.className = `status status-${type}`;
}

export function showLockIndicator(isLocked) {
    lockIndicator.className = isLocked ? "lock-indicator visible" : "lock-indicator";
    lockIndicator.textContent = isLocked ? "Locked" : "";
}

export function showParticipantCount(count) {
    participantCountElement.textContent = `${count} ${count === 1 ? "person" : "people"}`;
    participantCountElement.classList.remove("hidden");
}

export function hideParticipantCount() {
    participantCountElement.classList.add("hidden");
}
