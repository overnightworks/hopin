const greeting = document.getElementById("homeGreeting");

export function showGreeting(name) {
    if (name !== null) greeting.textContent = `Hi, ${name}`;
    greeting.removeAttribute("aria-busy");
}

export function initHome({ onStartRequested }) {
    document.getElementById("homeStartBtn").addEventListener("click", onStartRequested);
    greeting.setAttribute("aria-busy", "true");
}
