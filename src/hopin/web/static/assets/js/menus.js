const menus = [];

function closeMenu({ button, dialog, scrim }) {
    scrim.classList.add("hidden");
    dialog.close();
    button.classList.remove("active");
    button.setAttribute("aria-expanded", "false");
    button.focus();
}

function openMenu(menu) {
    for (const other of menus) if (other !== menu && other.dialog.open) closeMenu(other);
    menu.scrim.classList.remove("hidden");
    menu.dialog.show();
    menu.button.classList.add("active");
    menu.button.setAttribute("aria-expanded", "true");
    const firstItems = menu.dialog.querySelectorAll(menu.firstFocus);
    [...firstItems].find((item) => item.checkVisibility()).focus();
}

function toggleMenu(menu) {
    if (menu.dialog.open) closeMenu(menu);
    else openMenu(menu);
}

export function closeOpenMenu() {
    const open = menus.find((menu) => menu.dialog.open);
    if (open) closeMenu(open);
}

export function registerMenu(menu) {
    menus.push(menu);
    menu.button.addEventListener("click", () => toggleMenu(menu));
    menu.scrim.addEventListener("click", closeOpenMenu);
}

export function initMenus() {
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeOpenMenu();
    });
}
