// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { registerMenu } from "#lib/ui/menus.ts";
import { SCREEN, screens, showScreen } from "#lib/ui/screens.svelte.ts";

function openRegisteredMenu() {
    const button = document.createElement("button");
    const dialog = document.createElement("dialog");
    const scrim = document.createElement("div");
    dialog.open = true;
    registerMenu({ button, dialog, scrim, firstFocus: "button" });
    // jsdom has no dialog methods or visibility check; the page's browsers do.
    dialog.close = () => {
        dialog.open = false;
    };
    dialog.show = () => {
        dialog.open = true;
    };
    Object.assign(dialog, { querySelectorAll: () => [{ checkVisibility: () => true, focus: () => undefined }] });
    button.click();
    return dialog;
}

describe("screens", () => {
    it("starts with no screen shown and shows the one asked for", () => {
        expect(screens.current).toBeNull();

        showScreen(SCREEN.PREJOIN);

        expect(screens.current).toBe(SCREEN.PREJOIN);
    });

    it("closes the open menu on a screen change", () => {
        const dialog = openRegisteredMenu();
        expect(dialog.open).toBe(true);

        showScreen(SCREEN.CALL);

        expect(dialog.open).toBe(false);
        expect(screens.current).toBe(SCREEN.CALL);
    });
});
