// @vitest-environment jsdom
import { flushSync } from "svelte";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installedParticipation, joined } from "./page.fixture.ts";
import { refusalOf } from "./offers.svelte.ts";
import { AGENT_REFUSAL, INVITATION_OUTCOME, PARTICIPATION_SIGNAL, TURN_OUTCOME } from "./wire.ts";

// The real chat module loads the call sounds, which build an audio context when first imported.
vi.hoisted(() => {
    globalThis.AudioContext = function AudioContextStub() {} as unknown as typeof AudioContext;
});

describe("calling an agent that has an account of its own", () => {
    afterEach(() => {
        vi.useRealTimers();
    });

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
        [TURN_OUTCOME.NOT_ENABLED, "not_enabled", "Nicht freigeschaltet"],
        [TURN_OUTCOME.BUSY, "busy", "Gerade ausgelastet"],
        [TURN_OUTCOME.BUDGET_SPENT, "budget_spent", "Budget aufgebraucht"],
        [TURN_OUTCOME.UNAVAILABLE, "unavailable", "Gerade gestört"],
    ])(
        "CALL-73: gives the tile and the here row of a seat that may send the agent away the outcome %s it stated",
        async (outcome, turnState, words) => {
            const page = await installedParticipation();
            page.seat(false);
            page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [], send_away: ["a1"] });
            page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

            page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome });

            expect([names(page), page.offers()[0]?.textContent, page.tiles.get("agent-a1")?.turnState]).toEqual([
                ["Claude"],
                expect.stringContaining(words),
                turnState,
            ]);
        },
    );

    it("CALL-73: gives the tile of a seat with no right to send the agent away the outcome and lists no here row", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [], send_away: [] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        expect([page.tiles.get("agent-a1")?.turnState, names(page), page.shown()]).toEqual(["busy", [], false]);
    });

    it("CALL-73: shows no outcome on the tile or beside the name of an agent whose outcome was cleared, and drops it with the agent", async () => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [], send_away: ["a1"] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: null });
        const cleared = [
            page.tiles.get("agent-a1")?.turnState,
            page.offers()[0]?.textContent.includes("Gerade ausgelastet"),
        ];
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });
        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });
        page.emit(PARTICIPATION_SIGNAL.AGENT_TURN_STATE, { agent_id: "a1", outcome: TURN_OUTCOME.BUSY });

        expect([cleared, names(page), page.tiles.size]).toEqual([[undefined, false], [], 0]);
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
        [INVITATION_OUTCOME.OWNER_AWAY, "Sein Besitzer ist nicht hier."],
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
            outcome: INVITATION_OUTCOME.OWNER_AWAY,
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
            outcome: INVITATION_OUTCOME.OWNER_AWAY,
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
            outcome: INVITATION_OUTCOME.OWNER_AWAY,
        });

        page.offers()[0]?.closest("dialog")?.dispatchEvent(new Event("close"));

        expect(page.offers()).toEqual([]);
    });

    // The kit's outcome lasts a few seconds; any longer wait than that reaches its end.
    const AFTER_THE_OUTCOME_MILLISECONDS = 60_000;

    it.each([INVITATION_OUTCOME.NO_ANSWER, INVITATION_OUTCOME.BUSY, INVITATION_OUTCOME.OWNER_AWAY])(
        "CALL-52: takes the %s row of an agent that is not offered away when its outcome has run out",
        async (outcome) => {
            vi.useFakeTimers();
            const page = await installedParticipation();
            page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [] });
            page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, { agent_key: "k1", name: "Mozart", outcome });

            flushSync();
            vi.advanceTimersByTime(AFTER_THE_OUTCOME_MILLISECONDS);
            flushSync();

            expect([names(page), page.buttons()]).toEqual([[], []]);
        },
    );

    it("CALL-52: shows the call key on the row of an agent that is still offered when its outcome has run out", async () => {
        vi.useFakeTimers();
        const page = await installedParticipation();
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, offers: [offered("Mozart")] });
        page.emit(PARTICIPATION_SIGNAL.AGENT_INVITATION_ENDED, {
            agent_key: "k1",
            name: "Mozart",
            outcome: INVITATION_OUTCOME.NO_ANSWER,
        });

        flushSync();
        vi.advanceTimersByTime(AFTER_THE_OUTCOME_MILLISECONDS);
        flushSync();

        expect([names(page), page.buttons().length, page.offers()[0]?.textContent]).toEqual([
            ["Mozart"],
            1,
            expect.not.stringContaining("Keine Antwort"),
        ]);
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
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined());

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, full: true, offers: [], send_away: ["a1"] });
        const notShown = [page.offers().length, (page.sentence() ?? "").includes("kein Platz")];
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: true, full: true, offers: [], send_away: ["a1"] });

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
        [AGENT_REFUSAL.NOT_LISTENING, "agent-unavailable"],
        [AGENT_REFUSAL.INVITED_BY_ANOTHER, "agent-unavailable"],
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

describe("sending an agent away", () => {
    const withTwoAgents = async (sendAway: string[]) => {
        const page = await installedParticipation();
        page.seat(false);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a2"));
        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [], send_away: sendAway });
        return page;
    };
    const askToLeave = (page: Awaited<ReturnType<typeof installedParticipation>>) =>
        page.buttons().filter((button) => button.getAttribute("aria-label")?.includes("Claude"));

    it("CALL-84: lists as here only the agents the last offers frame names for sending away, and a press asks the server to send that agent away", async () => {
        const page = await withTwoAgents(["a2"]);

        page.buttons()[0]?.click();

        expect([page.offers().length, page.sent]).toEqual([
            1,
            [{ type: PARTICIPATION_SIGNAL.AGENT_REMOVE, agent_id: "a2" }],
        ]);
    });

    it.each([
        { order: "the offers frame after the join", frameFirst: false },
        { order: "the offers frame before the join", frameFirst: true },
    ])("CALL-84: lists the agent for $order and takes it away with a later frame", async ({ frameFirst }) => {
        const page = await installedParticipation();
        page.seat(false);
        const frame = (sendAway: string[]) => {
            page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [], send_away: sendAway });
        };
        if (frameFirst) frame(["a1"]);
        page.emit(PARTICIPATION_SIGNAL.AGENT_JOINED, joined("a1"));
        if (!frameFirst) frame(["a1"]);
        const listed = page.offers().length;

        frame([]);

        expect([listed, page.offers().length, page.tiles.has("agent-a1")]).toEqual([1, 0, true]);
    });

    it("CALL-84: reads an offers frame without a send-away list as an empty one", async () => {
        const page = await withTwoAgents(["a1"]);

        page.emit(PARTICIPATION_SIGNAL.AGENT_OFFERS, { shown: false, offers: [] });

        expect(page.offers()).toEqual([]);
    });

    it("CALL-84: moves the focus that sat on the button of a row that leaves to the control", async () => {
        const page = await withTwoAgents(["a1", "a2"]);
        askToLeave(page)[0]?.focus();

        askToLeave(page)[0]?.click();
        page.emit(PARTICIPATION_SIGNAL.AGENT_LEFT, { agent_id: "a1" });

        expect([page.offers().length, page.focused()?.id]).toEqual([1, "agentOffers"]);
    });

    it.each([
        { reason: AGENT_REFUSAL.NOT_ALLOWED_TO_SEND_AWAY, kitReason: "not-allowed" },
        { reason: AGENT_REFUSAL.SEND_AWAY_UNAVAILABLE, kitReason: "try-again" },
    ])("CALL-84: maps the refused send-away $reason to the kit's $kitReason reason", ({ reason, kitReason }) => {
        expect(refusalOf(reason)).toBe(kitReason);
    });

    it.each([AGENT_REFUSAL.NOT_ALLOWED_TO_SEND_AWAY, AGENT_REFUSAL.SEND_AWAY_UNAVAILABLE])(
        "CALL-84: answers the refused send-away %s above the rows and shows no toast",
        async (reason) => {
            const page = await withTwoAgents(["a1"]);

            page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason });

            expect([page.alert(), page.toasts, page.offers().length]).toEqual([expect.any(String), [], 1]);
        },
    );

    it("CALL-84: takes a standing refusal away when the person presses a send-away", async () => {
        const page = await withTwoAgents(["a1"]);
        page.emit(PARTICIPATION_SIGNAL.AGENT_REFUSED, { reason: AGENT_REFUSAL.NOT_ALLOWED_TO_SEND_AWAY });

        askToLeave(page)[0]?.click();

        expect(page.alert()).toBeUndefined();
    });
});
