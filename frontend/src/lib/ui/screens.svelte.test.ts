// @vitest-environment jsdom
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";
import type { Menu } from "#lib/ui/menus.ts";

// jsdom implements neither of these; the page's browsers do.
const dialogPrototype = HTMLDialogElement.prototype as unknown as Record<string, unknown>;
const elementPrototype = Element.prototype as unknown as Record<string, unknown>;
const originalBrowserMethods = {
    show: dialogPrototype.show,
    close: dialogPrototype.close,
    checkVisibility: elementPrototype.checkVisibility,
};

afterEach(() => {
    dialogPrototype.show = originalBrowserMethods.show;
    dialogPrototype.close = originalBrowserMethods.close;
    elementPrototype.checkVisibility = originalBrowserMethods.checkVisibility;
});

// The screens module holds its current screen, so each case loads a fresh graph.
async function freshScreens() {
    vi.resetModules();
    const screens = await import("#lib/ui/screens.svelte.ts");
    const menus = await import("#lib/ui/menus.ts");
    return { ...screens, registerMenu: menus.registerMenu };
}

function stubBrowserMethods() {
    dialogPrototype.show = function (this: HTMLDialogElement) {
        this.setAttribute("open", "");
    };
    dialogPrototype.close = function (this: HTMLDialogElement) {
        this.removeAttribute("open");
    };
    elementPrototype.checkVisibility = () => true;
}

function openRegisteredMenu(registerMenu: (menu: Menu) => void) {
    stubBrowserMethods();
    const button = document.createElement("button");
    const dialog = document.createElement("dialog");
    dialog.append(document.createElement("button"));
    const scrim = document.createElement("div");
    document.body.append(button, dialog, scrim);
    onTestFinished(() => {
        button.remove();
        dialog.remove();
        scrim.remove();
    });
    registerMenu({ button, dialog, scrim, firstFocus: "button" });
    button.click();
    return dialog;
}

describe("screens", () => {
    it("starts with no screen shown and shows the one asked for", async () => {
        const { SCREEN, screens, showScreen } = await freshScreens();
        expect(screens.current).toBeNull();

        showScreen(SCREEN.PREJOIN);

        expect(screens.current).toBe(SCREEN.PREJOIN);
    });

    it("closes the open menu on a screen change", async () => {
        const { SCREEN, screens, showScreen, registerMenu } = await freshScreens();
        const dialog = openRegisteredMenu(registerMenu);
        expect(dialog.open).toBe(true);

        showScreen(SCREEN.CALL);

        expect(dialog.open).toBe(false);
        expect(screens.current).toBe(SCREEN.CALL);
    });
});
