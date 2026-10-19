const statusElement = document.getElementById("status");
const lockIndicator = document.getElementById("lockIndicator");
const participantCountElement = document.getElementById("participantCount");
const participantCountNumber = participantCountElement.querySelector(".num");

export function setStatus(text, type) {
    statusElement.textContent = text;
    statusElement.className = `status status-${type}`;
}

export function showLockIndicator(isLocked) {
    lockIndicator.className = isLocked ? "lock-indicator visible" : "lock-indicator";
    lockIndicator.textContent = isLocked ? "Locked" : "";
}

export function showParticipantCount(count) {
    participantCountNumber.textContent = count;
    participantCountElement.classList.remove("hidden");
}

export function hideParticipantCount() {
    participantCountElement.classList.add("hidden");
}
