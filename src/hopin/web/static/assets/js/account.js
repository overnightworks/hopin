import { registerMenu } from "./menus.js";
import { goThroughSignIn } from "./router.js";

const ACCOUNT_URL = "/api/account";
const SIGNED_OUT_STATUS = 401;
const INITIAL_SEPARATOR = /[._-]/;
const INITIAL_COUNT = 2;

function initialsOf(name) {
    const parts = name.split(INITIAL_SEPARATOR).filter(Boolean);
    return parts
        .slice(0, INITIAL_COUNT)
        .map((part) => [...part][0].toUpperCase())
        .join("");
}

async function readAccountName() {
    try {
        const answer = await fetch(ACCOUNT_URL);
        if (answer.status === SIGNED_OUT_STATUS) {
            goThroughSignIn();
            return null;
        }
        if (!answer.ok) return null;
        const { name } = await answer.json();
        return typeof name === "string" ? name : null;
    } catch {
        // The avatars keep the person icon when the name cannot be read.
        return null;
    }
}

function showAccountName(name) {
    const initials = initialsOf(name);
    for (const avatar of document.querySelectorAll(".account-button, #accountMenu .av")) avatar.textContent = initials;
    const nameLine = document.querySelector("#accountMenu .menu-head b");
    nameLine.textContent = name;
    nameLine.classList.remove("hidden");
}

function registerAccountMenu() {
    const dialog = document.getElementById("accountMenu");
    const scrim = document.getElementById("accountScrim");
    for (const button of document.querySelectorAll(".account-button")) {
        registerMenu({ button, dialog, firstFocus: ".mi", scrim });
    }
}

export function initAccount({ onNameRead }) {
    registerAccountMenu();
    void readAccountName().then((name) => {
        if (name !== null) showAccountName(name);
        onNameRead(name);
    });
}
