const ACCOUNT_URL = "/api/account";

async function greetMember() {
    try {
        const answer = await fetch(ACCOUNT_URL);
        if (!answer.ok) return;
        const { name } = await answer.json();
        if (typeof name === "string") document.getElementById("homeGreeting").textContent = `Hi, ${name}`;
    } catch {
        // The heading keeps "Hi there" when the name cannot be read.
    }
}

export function initHome({ onStartRequested }) {
    document.getElementById("homeStartBtn").addEventListener("click", onStartRequested);
    void greetMember();
}
