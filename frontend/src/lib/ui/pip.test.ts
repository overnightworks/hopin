// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { pictureInPicture } from "#lib/ui/pip.ts";

const STAGE_RECT = { left: 0, top: 0, width: 400, height: 800 };
const TILE_SIZE = 100;
const POINTER = 7;

interface Setup {
    stage: HTMLElement;
    tile: HTMLElement;
    capture: ReturnType<typeof vi.fn>;
    cleanup: () => void;
}

function setUp({ count = "2", phone = true }: { count?: string; phone?: boolean } = {}): Setup {
    const stage = document.createElement("div");
    stage.id = "stage";
    stage.dataset.count = count;
    stage.getBoundingClientRect = () => ({ ...STAGE_RECT }) as DOMRect;
    const tile = document.createElement("div");
    const capture = vi.fn();
    tile.setPointerCapture = capture;
    stage.append(tile);
    document.body.append(stage);
    vi.stubGlobal("matchMedia", () => ({ matches: phone }));
    return { stage, tile, capture, cleanup: pictureInPicture(tile) };
}

function firePointer(
    tile: HTMLElement,
    type: string,
    init: { id?: number; button?: number; x?: number; y?: number } = {},
) {
    const event = new Event(type) as Event & Record<string, number>;
    event.pointerId = init.id ?? POINTER;
    event.button = init.button ?? 0;
    event.clientX = init.x ?? 0;
    event.clientY = init.y ?? 0;
    tile.dispatchEvent(event);
}

function placeTileCentre(tile: HTMLElement, x: number, y: number) {
    tile.getBoundingClientRect = () =>
        ({ left: x - TILE_SIZE / 2, top: y - TILE_SIZE / 2, width: TILE_SIZE, height: TILE_SIZE }) as DOMRect;
}

afterEach(() => {
    document.body.replaceChildren();
    vi.unstubAllGlobals();
});

describe("picture in picture", () => {
    it.each([
        ["tl", 50, 100],
        ["tr", 350, 100],
        ["bl", 50, 700],
        ["br", 350, 700],
    ])("snaps to %s when released there", (corner, x, y) => {
        const { tile } = setUp();
        placeTileCentre(tile, x, y);

        firePointer(tile, "pointerdown");
        firePointer(tile, "pointerup");

        expect(tile.dataset.corner).toBe(corner);
        expect(tile.classList.contains("dragging")).toBe(false);
        expect(tile.style.translate).toBe("");
    });

    it("captures the pointer and follows its offset while dragging", () => {
        const { tile, capture } = setUp();

        firePointer(tile, "pointerdown", { x: 10, y: 20 });
        firePointer(tile, "pointermove", { x: 40, y: 15 });

        expect(capture).toHaveBeenCalledWith(POINTER);
        expect(tile.classList.contains("dragging")).toBe(true);
        expect(tile.style.translate).toBe("30px -5px");
    });

    it("snaps on cancel like on up", () => {
        const { tile } = setUp();
        placeTileCentre(tile, 350, 700);

        firePointer(tile, "pointerdown");
        firePointer(tile, "pointercancel");

        expect(tile.dataset.corner).toBe("br");
    });

    it("ignores a non-primary button", () => {
        const { tile, capture } = setUp();

        firePointer(tile, "pointerdown", { button: 2 });

        expect(capture).not.toHaveBeenCalled();
        expect(tile.classList.contains("dragging")).toBe(false);
    });

    it.each([
        ["a count other than two", { count: "3" }],
        ["a desktop", { phone: false }],
    ])("does not drag on %s", (_name, options) => {
        const { tile, capture } = setUp(options);

        firePointer(tile, "pointerdown");
        firePointer(tile, "pointermove", { x: 30, y: 30 });

        expect(capture).not.toHaveBeenCalled();
        expect(tile.classList.contains("dragging")).toBe(false);
        expect(tile.style.translate).toBe("");
    });

    it("changes nothing for another pointer's move and up", () => {
        const { tile } = setUp();
        placeTileCentre(tile, 350, 700);

        firePointer(tile, "pointerdown");
        firePointer(tile, "pointermove", { id: 9, x: 30, y: 30 });
        firePointer(tile, "pointerup", { id: 9 });

        expect(tile.style.translate).toBe("");
        expect(tile.dataset.corner).toBeUndefined();
        expect(tile.classList.contains("dragging")).toBe(true);
    });

    it("releases the tile without a corner when the call is outgrown during the move", () => {
        const { stage, tile } = setUp();
        firePointer(tile, "pointerdown");
        firePointer(tile, "pointermove", { x: 30, y: 30 });

        stage.dataset.count = "3";
        firePointer(tile, "pointermove", { x: 60, y: 60 });

        expect(tile.style.translate).toBe("");
        expect(tile.classList.contains("dragging")).toBe(false);
        expect(tile.dataset.corner).toBeUndefined();
    });

    it("releases the tile without a corner when the call is outgrown before the up", () => {
        const { stage, tile } = setUp();
        firePointer(tile, "pointerdown");

        stage.dataset.count = "3";
        firePointer(tile, "pointerup");

        expect(tile.classList.contains("dragging")).toBe(false);
        expect(tile.dataset.corner).toBeUndefined();
    });

    it("listens to nothing after the cleanup", () => {
        const { tile, capture, cleanup } = setUp();

        cleanup();
        firePointer(tile, "pointerdown");

        expect(capture).not.toHaveBeenCalled();
    });

    it("throws when the tile is outside the stage", () => {
        expect(() => pictureInPicture(document.createElement("div"))).toThrow("not inside the stage");
    });
});
