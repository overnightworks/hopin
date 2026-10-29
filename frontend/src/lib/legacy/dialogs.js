const leaveConfirm = document.getElementById("leaveConfirm");
const leaveCancelButton = document.getElementById("leaveCancel");
const leaveYesButton = document.getElementById("leaveYes");

export function showLeaveDialog() {
    leaveConfirm.classList.remove("hidden");
}

export function initDialogs({ onLeaveConfirmed }) {
    leaveCancelButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
    });
    leaveYesButton.addEventListener("click", () => {
        leaveConfirm.classList.add("hidden");
        onLeaveConfirmed();
    });
}
