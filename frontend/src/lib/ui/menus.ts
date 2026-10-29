export interface Menu {
    readonly button: HTMLButtonElement;
    readonly dialog: HTMLDialogElement;
    readonly scrim: HTMLElement;
    readonly firstFocus: string;
}

let openedMenu: Menu | null = null;

function closeMenu({ button, dialog, scrim }: Menu) {
    openedMenu = null;
    scrim.classList.add("hidden");
    dialog.close();
    button.classList.remove("active");
    button.setAttribute("aria-expanded", "false");
    button.focus();
}

function openMenu(menu: Menu) {
    if (openedMenu) closeMenu(openedMenu);
    openedMenu = menu;
    menu.scrim.classList.remove("hidden");
    menu.dialog.show();
    menu.button.classList.add("active");
    menu.button.setAttribute("aria-expanded", "true");
    const firstItems = menu.dialog.querySelectorAll<HTMLElement>(menu.firstFocus);
    const firstVisibleItem = [...firstItems].find((item) => item.checkVisibility());
    if (!firstVisibleItem) {
        throw new Error(`Menu has no visible item matching "${menu.firstFocus}" to focus`);
    }
    firstVisibleItem.focus();
}

function toggleMenu(menu: Menu) {
    if (openedMenu === menu) closeMenu(menu);
    else openMenu(menu);
}

export function closeOpenMenu() {
    if (openedMenu) closeMenu(openedMenu);
}

export function registerMenu(menu: Menu) {
    menu.button.addEventListener("click", () => {
        toggleMenu(menu);
    });
    menu.scrim.addEventListener("click", closeOpenMenu);
}

export function initMenus() {
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeOpenMenu();
    });
}
