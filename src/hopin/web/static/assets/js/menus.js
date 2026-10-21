let openedMenu = null;
const scrimsClosingTheMenu = new WeakSet();

function closeMenu({ button, dialog, scrim }) {
    openedMenu = null;
    scrim.classList.add("hidden");
    dialog.close();
    button.classList.remove("active");
    button.setAttribute("aria-expanded", "false");
    button.focus();
}

function openMenu(menu) {
    if (openedMenu) closeMenu(openedMenu);
    openedMenu = menu;
    menu.scrim.classList.remove("hidden");
    menu.dialog.show();
    menu.button.classList.add("active");
    menu.button.setAttribute("aria-expanded", "true");
    const firstItems = menu.dialog.querySelectorAll(menu.firstFocus);
    [...firstItems].find((item) => item.checkVisibility()).focus();
}

function toggleMenu(menu) {
    if (openedMenu === menu) closeMenu(menu);
    else openMenu(menu);
}

export function closeOpenMenu() {
    if (openedMenu) closeMenu(openedMenu);
}

export function registerMenu(menu) {
    menu.button.addEventListener("click", () => toggleMenu(menu));
    if (scrimsClosingTheMenu.has(menu.scrim)) return;
    scrimsClosingTheMenu.add(menu.scrim);
    menu.scrim.addEventListener("click", closeOpenMenu);
}

export function initMenus() {
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeOpenMenu();
    });
}
