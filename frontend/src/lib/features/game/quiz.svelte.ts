import type { OutgoingFrame } from "#lib/call/feature-port.ts";
import { GAME_PHASE, GAME_REFUSAL, GAME_SIGNAL } from "./wire.ts";

export type PhaseFrame =
    | { phase: GAME_PHASE.IDLE }
    | { phase: GAME_PHASE.THINKING | GAME_PHASE.ANSWERING; round: number; rounds: number; starter: string };

export interface LineFrame {
    text: string;
    round?: number;
}

export interface AnswerCountFrame {
    answered: number;
    players: number;
}

export interface RefusedFrame {
    reason: GAME_REFUSAL;
    retry_after_seconds?: number;
}

export type QuestionCardProps = {
    tag: string;
    question: string;
    canReveal: boolean;
    revealDisabled: boolean;
    locked: boolean;
    status: string;
    sent: boolean;
    reveal: () => void;
    answer: (text: string) => void;
};

interface QuizRound {
    round: number;
    rounds: number;
    starter: string;
}

interface QuizOutlet {
    send(frame: OutgoingFrame): void;
    isConnected(): boolean;
}

/** One page's view of the quiz; every change ends in `onChange`, which hands the page what to show. */
export class Quiz {
    readonly #outlet: QuizOutlet;
    readonly #onChange: () => void;
    #ownParticipantId: string | null = null;
    #isHost = false;
    #phase = GAME_PHASE.IDLE;
    #quizRound: QuizRound | null = null;
    #question: { round: number; text: string } | null = null;
    #awaitingAnswer = false;
    #answerCount: { answered: number; players: number } | null = null;
    #answerSent = false;
    #answerSentBeforeLastSend = false;
    #cardRound: number | null = null;
    #card: QuestionCardProps | null = null;

    constructor(outlet: QuizOutlet, onChange: () => void) {
        this.#outlet = outlet;
        this.#onChange = onChange;
    }

    get isThinking() {
        return this.#phase === GAME_PHASE.THINKING;
    }

    get isRunning() {
        return this.#phase === GAME_PHASE.ANSWERING;
    }

    get isEnding() {
        return this.isRunning && this.#quizRound !== null && this.#mayEnd(this.#quizRound);
    }

    get startDisabled() {
        return this.isThinking || (this.isRunning && !this.isEnding) || this.#awaitingAnswer;
    }

    get progress() {
        return this.#quizRound === null
            ? ""
            : `Question ${String(this.#quizRound.round)} of ${String(this.#quizRound.rounds)}`;
    }

    /** The open round's card props, a new object per round and changed in place after that. */
    get card() {
        return this.#card;
    }

    joined(participantId: string, isHost: boolean) {
        this.#ownParticipantId = participantId;
        this.#isHost = isHost;
        this.reset();
    }

    reset() {
        this.#phase = GAME_PHASE.IDLE;
        this.#quizRound = null;
        this.#question = null;
        this.#awaitingAnswer = false;
        this.#changed();
    }

    receivePhase(frame: PhaseFrame) {
        this.#phase = frame.phase;
        this.#awaitingAnswer = false;
        if (frame.phase === GAME_PHASE.IDLE) {
            this.#quizRound = null;
            this.#question = null;
        } else {
            this.#quizRound = { round: frame.round, rounds: frame.rounds, starter: frame.starter };
        }
        this.#changed();
    }

    receiveLine(frame: LineFrame) {
        // A line without a round is the reveal or the final score: the card shows only an open question.
        this.#question = typeof frame.round === "number" ? { round: frame.round, text: frame.text } : null;
        this.#changed();
    }

    receiveAnswerCount(frame: AnswerCountFrame) {
        this.#answerCount = { answered: frame.answered, players: frame.players };
        this.#changed();
    }

    receiveRefusal(frame: RefusedFrame) {
        this.#awaitingAnswer = false;
        if (frame.reason === GAME_REFUSAL.ANSWER_CLOSED) this.#answerSent = false;
        if (frame.reason === GAME_REFUSAL.ANSWER_TOO_LONG) this.#answerSent = this.#answerSentBeforeLastSend;
        this.#changed();
    }

    startOrEnd() {
        this.#ask(this.#phase === GAME_PHASE.IDLE ? GAME_SIGNAL.GAME_START : GAME_SIGNAL.GAME_END);
    }

    reveal() {
        this.#ask(GAME_SIGNAL.GAME_REVEAL);
    }

    answer(typed: string) {
        const text = typed.trim();
        if (text === "" || !this.#outlet.isConnected()) return;
        this.#outlet.send({ type: GAME_SIGNAL.GAME_ANSWER, text });
        this.#answerSentBeforeLastSend = this.#answerSent;
        this.#answerSent = true;
        this.#changed();
    }

    #mayEnd(quizRound: QuizRound) {
        return quizRound.starter === this.#ownParticipantId || this.#isHost;
    }

    #ask(signalType: GAME_SIGNAL) {
        if (!this.#outlet.isConnected()) return;
        this.#awaitingAnswer = true;
        this.#changed();
        this.#outlet.send({ type: signalType });
    }

    #changed() {
        this.#syncCard();
        this.#onChange();
    }

    #answerStatus() {
        if (this.#answerCount === null) return "";
        const count = `${String(this.#answerCount.answered)} of ${String(this.#answerCount.players)} answered`;
        return this.#answerSent ? `Answer sent · ${count}` : count;
    }

    #openCard(quizRound: QuizRound, question: { text: string }): QuestionCardProps {
        this.#answerCount = null;
        this.#answerSent = false;
        this.#answerSentBeforeLastSend = false;
        const card = $state<QuestionCardProps>({
            tag: `Question ${String(quizRound.round)} of ${String(quizRound.rounds)}`,
            question: question.text,
            canReveal: quizRound.starter === this.#ownParticipantId,
            revealDisabled: true,
            locked: true,
            status: "",
            sent: false,
            reveal: () => {
                this.reveal();
            },
            answer: (text: string) => {
                this.answer(text);
            },
        });
        return card;
    }

    #updateCard(card: QuestionCardProps) {
        const locked = !this.isRunning || this.#awaitingAnswer;
        card.revealDisabled = locked;
        card.locked = locked;
        card.status = this.#answerStatus();
        card.sent = this.#answerSent;
    }

    #syncCard() {
        // A page shows a round's card only once it holds that round's question; a seat seated later waits for the next.
        const quizRound = this.#quizRound;
        const question = this.#question;
        const hasOpenQuestion = quizRound !== null && question?.round === quizRound.round;
        const round = hasOpenQuestion ? quizRound.round : null;
        if (round !== this.#cardRound) {
            this.#cardRound = round;
            this.#card = hasOpenQuestion ? this.#openCard(quizRound, question) : null;
        }
        if (this.#card !== null) this.#updateCard(this.#card);
    }
}
