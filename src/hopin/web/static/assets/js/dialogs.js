const setupModal = document.getElementById("setupModal");
const setupCameraOff = document.getElementById("setupCamOff");
const setupStartButton = document.getElementById("setupStartBtn");
const leaveConfirm = document.getElementById("leaveConfirm");
const leaveCancelButton = document.getElementById("leaveCancel");
const leaveYesButton = document.getElementById("leaveYes");

export function showSetupDialog() {
    setupModal.classList.remove("hidden");
}

export function showLeaveDialog() {
    leaveConfirm.classList.remove("hidden");
}

export function initDialogs({ onSetupStart, onLeaveConfirmed }) {
    setupStartButton.addEventListener("click", () => {
        setupModal.classList.add("hidden");
        onSetupStart(!setupCameraOff.checked);
    });

    leaveCancelButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
    });
    leaveYesButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
        onLeaveConfirmed();
    });
}
