// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { closeOpenMenu, initMenus, registerMenu, type Menu } from "#lib/ui/menus.ts";

const FIRST_FOCUS = "dialog button";

// jsdom implements neither of these; the page's browsers do.
const dialogPrototype = HTMLDialogElement.prototype as unknown as Record<string, unknown>;
const elementPrototype = Element.prototype as unknown as Record<string, unknown>;
const originalDialogMethods = { show: dialogPrototype.show, close: dialogPrototype.close };
const originalCheckVisibility = elementPrototype.checkVisibility;

function stubBrowserMethods() {
    dialogPrototype.show = function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
    };
    dialogPrototype.close = function (this: HTMLDialogElement) {
        this.removeAttribute("open");
    };
    elementPrototype.checkVisibility = function (this: HTMLElement) {
        return !this.hidden;
    };
}

function restoreBrowserMethods() {
    dialogPrototype.show = originalDialogMethods.show;
    dialogPrototype.close = originalDialogMethods.close;
    elementPrototype.checkVisibility = originalCheckVisibility;
}

function buildMenu(scrim: HTMLElement, itemCount = 2): Menu {
    const button = document.createElement("button");
    const dialog = document.createElement("dialog");
    for (let index = 0; index < itemCount; index += 1) {
        dialog.append(document.createElement("button"));
    }
    document.body.append(button, dialog);
    return { button, dialog, scrim, firstFocus: FIRST_FOCUS };
}

function buildScrim() {
    const scrim = document.createElement("div");
    scrim.className = "hidden";
    document.body.append(scrim);
    return scrim;
}

function registeredMenu(scrim = buildScrim(), itemCount = 2) {
    const menu = buildMenu(scrim, itemCount);
    registerMenu(menu);
    return menu;
}

beforeEach(stubBrowserMethods);

afterEach(() => {
    closeOpenMenu();
    restoreBrowserMethods();
    document.body.replaceChildren();
});

describe("menus", () => {
    it("opens a menu with nothing open: scrim shown, dialog open, button active, first item focused", () => {
        const menu = registeredMenu();

        menu.button.click();

        expect(menu.scrim.classList.contains("hidden")).toBe(false);
        expect(menu.dialog.open).toBe(true);
        expect(menu.button.classList.contains("active")).toBe(true);
        expect(menu.button.getAttribute("aria-expanded")).toBe("true");
        expect(document.activeElement).toBe(menu.dialog.firstElementChild);
    });

    it("closes the open menu when another one opens", () => {
        const first = registeredMenu();
        const second = registeredMenu();
        first.button.click();

        second.button.click();

        expect(first.dialog.open).toBe(false);
        expect(first.button.getAttribute("aria-expanded")).toBe("false");
        expect(second.dialog.open).toBe(true);
    });

    it("closes the menu when its button is clicked again and returns focus to the button", () => {
        const menu = registeredMenu();
        menu.button.click();

        menu.button.click();

        expect(menu.dialog.open).toBe(false);
        expect(menu.scrim.classList.contains("hidden")).toBe(true);
        expect(menu.button.classList.contains("active")).toBe(false);
        expect(document.activeElement).toBe(menu.button);
    });

    it("does nothing when no menu is open", () => {
        const menu = registeredMenu();

        closeOpenMenu();

        expect(menu.dialog.open).toBe(false);
    });

    it("focuses the first visible item, skipping an invisible one", () => {
        const menu = registeredMenu();
        (menu.dialog.firstElementChild as HTMLElement).hidden = true;

        menu.button.click();

        expect(document.activeElement).toBe(menu.dialog.lastElementChild);
    });

    it("closes the menu when the scrim is clicked", () => {
        const menu = registeredMenu();
        menu.button.click();

        menu.scrim.click();

        expect(menu.dialog.open).toBe(false);
    });

    it("registers a scrim shared by two menus once, so one click closes without error", () => {
        const scrim = buildScrim();
        const first = registeredMenu(scrim);
        const second = registeredMenu(scrim);
        second.button.click();

        scrim.click();

        expect(second.dialog.open).toBe(false);
        expect(first.dialog.open).toBe(false);
    });

    it("closes the menu on Escape and ignores other keys", () => {
        initMenus();
        const menu = registeredMenu();
        menu.button.click();

        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter" }));
        expect(menu.dialog.open).toBe(true);

        document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
        expect(menu.dialog.open).toBe(false);
    });

    it("fails loud naming the focus target when no item is visible", () => {
        const menu = registeredMenu(buildScrim(), 0);
        const failures: unknown[] = [];
        const collect = (event: ErrorEvent) => {
            event.preventDefault();
            failures.push(event.error);
        };
        window.addEventListener("error", collect);

        menu.button.click();
        window.removeEventListener("error", collect);

        expect(failures).toHaveLength(1);
        expect((failures[0] as Error).message).toContain(FIRST_FOCUS);
    });
});
