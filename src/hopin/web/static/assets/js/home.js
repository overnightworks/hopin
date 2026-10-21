const ACCOUNT_URL = "/api/account";

async function greetMember() {
    const greeting = document.getElementById("homeGreeting");
    greeting.setAttribute("aria-busy", "true");
    try {
        const answer = await fetch(ACCOUNT_URL);
        if (!answer.ok) return;
        const { name } = await answer.json();
        if (typeof name === "string") greeting.textContent = `Hi, ${name}`;
    } catch {
        // The heading keeps "Hi there" when the name cannot be read.
    } finally {
        greeting.removeAttribute("aria-busy");
    }
}

export function initHome({ onStartRequested }) {
    document.getElementById("homeStartBtn").addEventListener("click", onStartRequested);
    void greetMember();
}
