import { describe, expect, it } from "vitest";
import vocabulary from "overnightworks-figure/vocabulary.json";
import { FIGURE_GESTURE, FIGURE_MOOD } from "#lib/features/participation/wire.ts";

describe("the gestures and moods an agent can name", () => {
    it.each([
        { kind: "gesture", named: Object.values(FIGURE_GESTURE), known: vocabulary.gestures },
        { kind: "mood", named: Object.values(FIGURE_MOOD), known: vocabulary.moods },
    ])("CALL-64, CALL-67: every $kind agents names is one figure's vocabulary holds", ({ named, known }) => {
        const unknownToFigure = named.filter((name) => !known.includes(name));

        expect(unknownToFigure).toEqual([]);
    });
});
