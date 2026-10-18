const passwordModal = document.getElementById("passwordModal");
const joinPasswordInput = document.getElementById("joinPasswordInput");
const joinPasswordButton = document.getElementById("joinPasswordBtn");
const setupModal = document.getElementById("setupModal");
const setupPasswordInput = document.getElementById("setupPasswordInput");
const setupCameraOff = document.getElementById("setupCamOff");
const setupStartButton = document.getElementById("setupStartBtn");
const leaveConfirm = document.getElementById("leaveConfirm");
const leaveCancelButton = document.getElementById("leaveCancel");
const leaveYesButton = document.getElementById("leaveYes");

export function showSetupDialog() {
    setupModal.classList.remove("hidden");
}

export function showPasswordDialog() {
    passwordModal.classList.remove("hidden");
}

export function showLeaveDialog() {
    leaveConfirm.classList.remove("hidden");
}

function pressButtonOnEnter(input, button) {
    input.addEventListener("keydown", (event) => {
        if (event.key === "Enter") button.click();
    });
}

export function initDialogs({ onSetupStart, onPasswordSubmit, onLeaveConfirmed }) {
    joinPasswordButton.addEventListener("click", () => {
        passwordModal.classList.add("hidden");
        onPasswordSubmit(joinPasswordInput.value);
    });
    pressButtonOnEnter(joinPasswordInput, joinPasswordButton);

    setupStartButton.addEventListener("click", () => {
        setupModal.classList.add("hidden");
        onSetupStart(setupPasswordInput.value || null, !setupCameraOff.checked);
    });
    pressButtonOnEnter(setupPasswordInput, setupStartButton);

    leaveCancelButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
    });
    leaveYesButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
        onLeaveConfirmed();
    });
}
