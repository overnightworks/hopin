// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { installedParticipation, joined } from "./page.fixture.ts";
import { refusalOf } from "./offers.svelte.ts";
import { AGENT_REFUSAL, INVITATION_OUTCOME, PARTICIPATION_SIGNAL, TURN_OUTCOME } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

describe("calling an agent that has an account of its own", () => {
    const offered = (name: string, pending = false, key = "k1") => ({ agent_key: key, name, pending });
    const names = (page: Awaited<ReturnType<typeof installedParticipation>>) =>
        page.offers().map((row) => row.querySelector(".name")?.textContent);

    it("CALL-51: lists an offered agent per row, and calling one asks the server to add that agent", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, {
            shown: true,
            offers: [offered("Mozart"), offered("Bach", false, "k2")],
        });
        page.buttons()[1]?.click();

        expect([names(page), page.sent]).toEqual([
            ["Mozart", "Bach"],
            [{ type: PARTICIPATION_SIGNAL.AGENT_ADD, agent_key: "k2" }],
        ]);
    });

    it("CALL-51: shows an open invitation as a row with no button, so it cannot be called again", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect([names(page), page.buttons()]).toEqual([["Mozart"], []]);
    });

    it.each([
        [TURN_OUTCOME.NOT_ENABLED, "Nicht freigeschaltet"],
        [TURN_OUTCOME.BUSY, "Gerade ausgelastet"],
        [TURN_OUTCOME.BUDGET_SPENT, "Budget aufgebraucht"],
        [TURN_OUTCOME.UNAVAILABLE, "Gerade gestört"],
    ])("CALL-73: shows the outcome %s an agent in the call stated beside its name", async (outcome, words) => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome });

        expect([names(page), page.offers()[0]?.textContent]).toEqual([["Claude"], expect.stringContaining(words)]);
    });

    it("CALL-73: shows the outcome an agent in the call stated to a seat that may not add agents", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        expect([names(page), page.offers()[0]?.textContent, page.buttons()]).toEqual([
            ["Claude"],
            expect.stringContaining("Gerade ausgelastet"),
            [],
        ]);
    });

    it("CALL-73: shows no outcome beside an agent whose outcome was cleared, and drops it with the agent", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: null });
        const cleared = page.offers()[0]?.textContent.includes("Gerade ausgelastet");
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });
        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        expect([cleared, names(page)]).toEqual([false, []]);
    });

    it("follows each frame exactly: the rows of the last frame are all there is", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });
        const pending = page.buttons().length;
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });

        expect([pending, names(page)]).toEqual([0, []]);
    });

    it.each([
        [INVITATION_OUTCOME.NO_ANSWER, "Keine Antwort"],
        [INVITATION_OUTCOME.BUSY, "Gerade beschäftigt"],
        [INVITATION_OUTCOME.OWNER_ABSENT, "Sein Besitzer ist nicht hier."],
    ])(
        "CALL-52: shows an invitation that ended as %s as the kit's row for it, with no call button",
        async (outcome, words) => {
            const page = await installedParticipation();
            page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });
            page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

            page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, { agent_key: "k1", name: "Mozart", outcome });

            expect([page.offers()[0]?.textContent, page.buttons()]).toEqual([expect.stringContaining(words), []]);
        },
    );

    it("CALL-52: keeps an agent's no-answer row while the list still offers it unchanged", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).toContain("Keine Antwort");
    });

    it("CALL-52: shows a new call of the agent as inviting again, not as its earlier outcome", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-52: shows no earlier outcome of an agent that returns to the list after leaving it", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-52: shows the owner-away row of an agent that is not listed, and keeps it while it stays unlisted", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.OWNER_ABSENT,
        });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Bach", false, "k2")] });

        expect(page.offers().map((row) => row.textContent)).toEqual([
            expect.stringContaining("Bach"),
            expect.stringContaining("Sein Besitzer ist nicht hier."),
        ]);
    });

    it("CALL-52: takes the owner-away row away once the agent is listed again", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.OWNER_ABSENT,
        });

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).not.toContain("Sein Besitzer ist nicht hier.");
    });

    it("CALL-52: takes the owner-away row away when the person closes More", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.OWNER_ABSENT,
        });

        page.offers()[0]?.closest("dialog")?.dispatchEvent(new Event("close"));

        expect(page.offers()).toEqual([]);
    });

    it("CALL-52: takes an outcome away with the control at a fresh join", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });
        page.seat(false);

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        expect(page.offers()[0]?.textContent).not.toContain("Keine Antwort");
    });

    it("CALL-51: keeps the control with its one sentence while no agent is listed, and shows none when it is not shown", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
        const whileEmpty = page.shown();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        expect([whileEmpty, page.shown()]).toEqual([true, false]);
    });

    it("CALL-51: says that no place is free, in place of the empty sentence, when the frame tells the call is full", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: false, offers: [] });
        const whileFree = page.sentence();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: true, offers: [] });
        const whileFull = page.sentence();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: false, offers: [] });

        expect([whileFree, whileFull, page.sentence()]).toEqual([
            expect.stringContaining("kein Agent erreichbar"),
            expect.stringContaining("kein Platz"),
            whileFree,
        ]);
    });

    it("CALL-51: says that no place is free beside the agents in the call only to a seat shown the control", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("someone"));

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, full: true, offers: [] });
        const notShown = [page.offers().length, (page.sentence() ?? "").includes("kein Platz")];
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: true, offers: [] });

        expect([notShown, [page.offers().length, (page.sentence() ?? "").includes("kein Platz")]]).toEqual([
            [1, false],
            [1, true],
        ]);
    });

    it("keeps the agent's name as text, never as markup", async () => {
        const page = await installedParticipation();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("<b>Mozart</b>")] });

        expect([names(page), page.offers()[0]?.querySelector("b")]).toEqual([["<b>Mozart</b>"], null]);
    });

    it("moves focus to the control when the row whose button had focus turns inviting", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.buttons()[0]?.focus();

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart", true)] });

        expect(page.focused()?.id).toBe("agentOffers");
    });

    it.each([
        [AGENT_REFUSAL.NOT_ADDER, "not-allowed"],
        [AGENT_REFUSAL.NO_SEAT, "owner-absent"],
        [AGENT_REFUSAL.SPONSOR_AWAY, "owner-absent"],
        [AGENT_REFUSAL.BUSY, "place-full"],
        [AGENT_REFUSAL.ALREADY_THERE, "agent-unavailable"],
        [AGENT_REFUSAL.GONE, "agent-unavailable"],
        [AGENT_REFUSAL.TWO_SEATS, "agent-unavailable"],
        [AGENT_REFUSAL.UNAVAILABLE, "try-again"],
        ["another_reason", "try-again"],
    ])("CALL-51: maps the refused add %s to the kit's %s reason", (reason, kitReason) => {
        expect(refusalOf(reason)).toBe(kitReason);
    });

    it("CALL-51: shows a refused add as one of the kit's sentences", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        expect(page.alert()).toBeTruthy();
    });

    it("CALL-51: keeps a refusal that follows the offers frame taking the control away", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.NOT_ADDER });

        expect(page.alert()).toBeTruthy();
    });

    it("takes the refusal away when the person calls again", async () => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        page.buttons()[0]?.click();

        expect(page.alert()).toBeUndefined();
    });

    it.each([
        {
            when: "a fresh join",
            end: (page: Awaited<ReturnType<typeof installedParticipation>>) => {
                page.seat(false);
            },
        },
        {
            when: "the end of the call",
            end: (page: Awaited<ReturnType<typeof installedParticipation>>) => {
                page.endCall();
            },
        },
    ])("takes the control and its refusal away at $when", async ({ end }) => {
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.BUSY });

        end(page);

        expect([page.shown(), page.alert()]).toEqual([false, undefined]);
    });
});
