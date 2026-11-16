import { describe, expect, it } from "vitest";
import vocabulary from "overnightworks-figure/vocabulary.json";
import { FIGURE_GESTURE } from "#lib/features/participation/wire.ts";

describe("the gestures an agent can name", () => {
    it("CALL-64: every gesture agents names is one figure's vocabulary holds", () => {
        const unknownToFigure = Object.values(FIGURE_GESTURE).filter(
            (gesture) => !vocabulary.gestures.includes(gesture),
        );

        expect(unknownToFigure).toEqual([]);
    });
});
