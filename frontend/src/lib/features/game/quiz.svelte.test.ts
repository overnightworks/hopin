import { describe, expect, it } from "vitest";
import type { OutgoingFrame } from "#lib/call/feature-port.ts";
import { Quiz, type PhaseFrame } from "./quiz.svelte.ts";
import { GAME_PHASE, GAME_REFUSAL, GAME_SIGNAL } from "./wire.ts";

const ME = "me";
const SOMEONE = "someone";

function answering(round: number, starter = ME): PhaseFrame {
    return { phase: GAME_PHASE.ANSWERING, round, rounds: 3, starter };
}

// A quiz on a connected page, seated as `ME`; `sent` collects the frames it sends.
function seatedQuiz({ isHost = false, connected = true } = {}) {
    const sent: OutgoingFrame[] = [];
    const seenProgress: string[] = [];
    const outlet = { send: (frame: OutgoingFrame) => void sent.push(frame), isConnected: () => connected };
    const quiz: Quiz = new Quiz(outlet, () => void seenProgress.push(quiz.progress));
    quiz.joined(ME, isHost);
    return { quiz, sent, seenProgress };
}

function openRound(quiz: Quiz, round: number, starter = ME) {
    quiz.receivePhase(answering(round, starter));
    quiz.receiveLine({ text: `Question ${String(round)}?`, round });
}

function openCard(quiz: Quiz) {
    const { card } = quiz;
    if (card === null) throw new Error("no card is open");
    return card;
}

describe("the start and end control", () => {
    it("starts a quiz while none runs and ends it while one does", () => {
        const { quiz, sent } = seatedQuiz();

        quiz.startOrEnd();
        quiz.receivePhase(answering(1));
        quiz.startOrEnd();

        expect(sent).toEqual([{ type: GAME_SIGNAL.GAME_START }, { type: GAME_SIGNAL.GAME_END }]);
    });

    it("sends nothing and waits for nothing while the call is not connected", () => {
        const { quiz, sent } = seatedQuiz({ connected: false });

        quiz.startOrEnd();

        expect(sent).toEqual([]);
        expect(quiz.startDisabled).toBe(false);
    });

    it("disables itself until the game master answers a request", () => {
        const { quiz } = seatedQuiz();
        expect(quiz.startDisabled).toBe(false);

        quiz.startOrEnd();
        expect(quiz.startDisabled).toBe(true);
        quiz.receivePhase({ phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME });

        expect(quiz.isThinking).toBe(true);
        expect(quiz.startDisabled).toBe(true);
        quiz.receivePhase(answering(1));
        expect(quiz.startDisabled).toBe(false);
    });

    it.each([
        ["the person who started it", ME, false, true],
        ["the host", SOMEONE, true, true],
        ["anyone else", SOMEONE, false, false],
    ])("lets %s end a running quiz: %s", (_who, starter, isHost, mayEnd) => {
        const { quiz } = seatedQuiz({ isHost });

        quiz.receivePhase(answering(1, starter));

        expect(quiz.isRunning).toBe(true);
        expect(quiz.isEnding).toBe(mayEnd);
        expect(quiz.startDisabled).toBe(!mayEnd);
    });

    it("is not ending while no quiz runs", () => {
        const { quiz } = seatedQuiz({ isHost: true });

        expect(quiz.isEnding).toBe(false);
        expect(quiz.isRunning).toBe(false);
    });
});

describe("the progress line", () => {
    it("names the round while a quiz runs and clears when it ends", () => {
        const { quiz } = seatedQuiz();
        expect(quiz.progress).toBe("");

        quiz.receivePhase(answering(2));
        expect(quiz.progress).toBe("Question 2 of 3");
        quiz.receivePhase({ phase: GAME_PHASE.IDLE });

        expect(quiz.progress).toBe("");
    });

    it("is already updated when the page is told of a change", () => {
        const { quiz, seenProgress } = seatedQuiz();

        quiz.receivePhase(answering(1));

        expect(seenProgress.at(-1)).toBe("Question 1 of 3");
    });
});

describe("the question card", () => {
    it("opens only once the page holds the open round's question", () => {
        const { quiz } = seatedQuiz();

        quiz.receivePhase(answering(2));
        expect(quiz.card).toBeNull();
        quiz.receiveLine({ text: "Question 1?", round: 1 });
        expect(quiz.card).toBeNull();
        quiz.receiveLine({ text: "Question 2?", round: 2 });

        expect(quiz.card).toMatchObject({ tag: "Question 2 of 3", question: "Question 2?" });
    });

    it("closes on a line without a round, which is the reveal or the final score", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        expect(quiz.card).not.toBeNull();

        quiz.receiveLine({ text: "It was Paris." });

        expect(quiz.card).toBeNull();
    });

    it("closes when the quiz ends or the call does", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        quiz.receivePhase({ phase: GAME_PHASE.IDLE });
        expect(quiz.card).toBeNull();

        openRound(quiz, 1);
        expect(quiz.card).not.toBeNull();
        quiz.reset();

        expect(quiz.card).toBeNull();
    });

    it("is one object per round, changed in place, and a new one for the next round", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        const first = openCard(quiz);

        quiz.receiveAnswerCount({ answered: 1, players: 2 });
        quiz.receivePhase(answering(1));
        expect(quiz.card).toBe(first);
        expect(first.status).toBe("1 of 2 answered");
        openRound(quiz, 2);

        expect(quiz.card).not.toBe(first);
        expect(openCard(quiz).tag).toBe("Question 2 of 3");
    });

    it.each([
        ["the person who started the quiz", ME, true],
        ["anyone else", SOMEONE, false],
    ])("offers Reveal to %s: %s", (_who, starter, canReveal) => {
        const { quiz } = seatedQuiz();

        openRound(quiz, 1, starter);

        expect(openCard(quiz).canReveal).toBe(canReveal);
    });

    it("unlocks while a question is open and locks while the game master answers", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);
        expect(card).toMatchObject({ locked: false, revealDisabled: false });

        quiz.receivePhase({ phase: GAME_PHASE.THINKING, round: 1, rounds: 3, starter: ME });
        expect(card).toMatchObject({ locked: true, revealDisabled: true });
        quiz.receivePhase(answering(1));

        expect(card).toMatchObject({ locked: false, revealDisabled: false });
    });

    it("locks after Reveal is sent and unlocks with the next phase frame", () => {
        const { quiz, sent } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);

        card.reveal();
        expect(sent).toEqual([{ type: GAME_SIGNAL.GAME_REVEAL }]);
        expect(card.revealDisabled).toBe(true);
        quiz.receivePhase(answering(1));

        expect(card.revealDisabled).toBe(false);
    });
});

describe("answering", () => {
    it("sends the trimmed answer and marks it sent", () => {
        const { quiz, sent } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);
        expect(card.sent).toBe(false);

        card.answer("  Paris  ");

        expect(sent).toEqual([{ type: GAME_SIGNAL.GAME_ANSWER, text: "Paris" }]);
        expect(card.sent).toBe(true);
    });

    it.each([
        ["an empty answer", "   ", true],
        ["an answer while the call is not connected", "Paris", false],
    ])("sends nothing for %s", (_label, text, connected) => {
        const { quiz, sent } = seatedQuiz({ connected });
        openRound(quiz, 1);

        openCard(quiz).answer(text);

        expect(sent).toEqual([]);
        expect(openCard(quiz).sent).toBe(false);
    });

    it("shows the count of answers, with the own answer in front once sent", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);
        expect(card.status).toBe("");

        quiz.receiveAnswerCount({ answered: 1, players: 3 });
        expect(card.status).toBe("1 of 3 answered");
        card.answer("Paris");

        expect(card.status).toBe("Answer sent · 1 of 3 answered");
    });

    it("forgets the answers of the last round when a new round opens", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        quiz.receiveAnswerCount({ answered: 2, players: 3 });
        openCard(quiz).answer("Paris");

        openRound(quiz, 2);

        expect(openCard(quiz)).toMatchObject({ status: "", sent: false });
    });

    it("is unsent again when the question was already closed", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        openCard(quiz).answer("Paris");

        quiz.receiveRefusal({ reason: GAME_REFUSAL.ANSWER_CLOSED });

        expect(openCard(quiz).sent).toBe(false);
    });

    it("keeps an earlier answer when the next one was too long, and has none when the first was", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);
        card.answer("Paris");
        card.answer("Paris, France, ...");

        quiz.receiveRefusal({ reason: GAME_REFUSAL.ANSWER_TOO_LONG });
        expect(card.sent).toBe(true);
        openRound(quiz, 2);
        const next = openCard(quiz);
        next.answer("x".repeat(500));
        quiz.receiveRefusal({ reason: GAME_REFUSAL.ANSWER_TOO_LONG });

        expect(next.sent).toBe(false);
    });

    it("keeps the answer for a refusal that does not concern it, and unlocks", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        const card = openCard(quiz);
        card.answer("Paris");
        card.reveal();
        expect(card.locked).toBe(true);

        quiz.receiveRefusal({ reason: GAME_REFUSAL.NOT_STARTER });

        expect(card).toMatchObject({ sent: true, locked: false });
    });
});

describe("a seat that joins or a call that ends", () => {
    it("starts from an idle quiz whatever it held before", () => {
        const { quiz } = seatedQuiz();
        openRound(quiz, 1);
        quiz.startOrEnd();

        quiz.joined(SOMEONE, false);

        expect(quiz).toMatchObject({ isRunning: false, isThinking: false, startDisabled: false, card: null });
    });

    it("takes the new seat's identity", () => {
        const { quiz } = seatedQuiz();

        quiz.joined(SOMEONE, false);
        quiz.receivePhase(answering(1, SOMEONE));

        expect(quiz.isEnding).toBe(true);
    });
});
