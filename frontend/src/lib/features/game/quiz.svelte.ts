import { SvelteSet } from "svelte/reactivity";
import { words } from "#lib/words.ts";
import { AGENT_ACTIVITY, type AgentTile, type OutgoingFrame } from "#lib/call/feature-port.ts";
import { GAME_PHASE, GAME_REFUSAL, GAME_SIGNAL, type GameFrameByType, type ScoreEntry } from "./wire.ts";

type PhaseFrame = GameFrameByType[GAME_SIGNAL.GAME_PHASE];
type LineFrame = GameFrameByType[GAME_SIGNAL.GAME_LINE];
type AnswerCountFrame = GameFrameByType[GAME_SIGNAL.GAME_ANSWER_COUNT];
type RefusedFrame = GameFrameByType[GAME_SIGNAL.GAME_REFUSED];

export type QuestionCardProps = {
    tag: string;
    question: string;
    canReveal: boolean;
    revealDisabled: boolean;
    locked: boolean;
    status: string;
    score: string;
    sent: boolean;
    reveal: () => void;
    answer: (text: string) => void;
};

/** Whether the person's answer went out, and whether a count that includes it has come back since. */
enum OwnAnswer {
    NONE,
    SENT,
    COUNTED,
}

interface QuizRound {
    round: number;
    rounds: number;
    starter: string;
}

interface QuizOutlet {
    send(frame: OutgoingFrame): void;
    isConnected(): boolean;
}

function scoreEntries(scores: ScoreEntry[]) {
    return scores
        .map((entry) => words["game.scoreEntry"]({ name: entry.name, correct: String(entry.correct) }))
        .join(", ");
}

/** The final score as the page writes it from the server's count, whatever the game master's last line says. */
export function finalScoreText(scores: ScoreEntry[]) {
    return words["game.finalScoreLine"]({ scores: scoreEntries(scores) });
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
    // The score the server counted from the verdicts of the reveals so far.
    #scores: ScoreEntry[] = [];
    #ownAnswer = OwnAnswer.NONE;
    #ownAnswerBeforeLastSend = OwnAnswer.NONE;
    #cardRound: number | null = null;
    #card: QuestionCardProps | null = null;
    // Kept across a reset: a resumed seat is replayed frames it already played.
    readonly #heardVoices = new SvelteSet<string>();
    // The voices being fetched or played: only these may start or end, so a late callback after a reset changes nothing.
    readonly #voicesOutstanding = new SvelteSet<string>();
    readonly #voicesPlaying = new SvelteSet<string>();
    // The last line was voiced and its voice frame has not come yet.
    #voiceFrameDue = false;
    // A line of this quiz was voiced, so the person can mute the voice; the choice lasts for the call.
    #voicesOffered = false;
    #voiceMuted = false;

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
        return this.#quizRound === null ? "" : this.#questionProgress(this.#quizRound);
    }

    /** The open round's card props, a new object per round and changed in place after that. */
    get card() {
        return this.#card;
    }

    /**
     * The game master's tile: it shows while the quiz runs and, after the final line's idle, while that
     * line's voice frame is due or its voice is fetched or played.
     */
    get agentTile(): AgentTile | null {
        const speaking = this.#voicesPlaying.size > 0;
        if (this.#phase === GAME_PHASE.IDLE && !this.#voiceFrameDue && this.#voicesOutstanding.size === 0) return null;
        if (speaking) return { name: words["game.masterName"], activity: AGENT_ACTIVITY.SPEAKING };
        const activity = this.isThinking ? AGENT_ACTIVITY.THINKING : AGENT_ACTIVITY.WAITING;
        return { name: words["game.masterName"], activity };
    }

    /** Whether a voiced line of this quiz played or plays, for as long as the quiz master's tile shows. */
    get voiceToggleShown() {
        return this.#voicesOffered && this.agentTile !== null;
    }

    get voiceMuted() {
        return this.#voiceMuted;
    }

    toggleVoiceMuted() {
        this.#voiceMuted = !this.#voiceMuted;
        this.#changed();
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
        this.#scores = [];
        this.#awaitingAnswer = false;
        this.#voiceMuted = false;
        this.receiveEnd();
    }

    /** The quiz was ended: its voices stop with it, so the tile goes with the phase. */
    receiveEnd() {
        this.#voiceFrameDue = false;
        this.#voicesOffered = false;
        this.#voicesOutstanding.clear();
        this.#voicesPlaying.clear();
        this.#changed();
    }

    receivePhase(frame: PhaseFrame) {
        if (this.#phase === GAME_PHASE.IDLE && frame.phase !== GAME_PHASE.IDLE) this.#voicesOffered = false;
        this.#phase = frame.phase;
        this.#awaitingAnswer = false;
        if (frame.phase === GAME_PHASE.IDLE) {
            this.#quizRound = null;
            this.#question = null;
            this.#scores = [];
        } else {
            this.#quizRound = { round: frame.round, rounds: frame.rounds, starter: frame.starter };
        }
        this.#changed();
    }

    receiveLine(frame: LineFrame) {
        // A line without a round is the reveal or the final score: the card shows only an open question.
        this.#question = frame.round === undefined ? null : { round: frame.round, text: frame.text };
        if (frame.scores !== undefined) this.#scores = frame.scores;
        this.#voiceFrameDue = frame.voiced === true;
        this.#voicesOffered ||= this.#voiceFrameDue;
        this.#changed();
    }

    /** Whether this is the first time the page is offered the voice, so a replayed one plays nothing. */
    isFirstHearing(voiceId: string) {
        const isFirst = !this.#heardVoices.has(voiceId);
        this.#heardVoices.add(voiceId);
        return isFirst;
    }

    /** A voice frame the page will play: from here until it ends or fails, the tile stays. */
    voiceRequested(voiceId: string) {
        this.#voicesOutstanding.add(voiceId);
        this.#changed();
    }

    /** The last voiced line's voice frame came, with a voice to play or without one: it is no longer due. */
    voiceFrameArrived() {
        this.#voiceFrameDue = false;
        this.#changed();
    }

    voiceStarted(voiceId: string) {
        this.#voicesPlaying.add(voiceId);
        this.#changed();
    }

    voiceEnded(voiceId: string) {
        if (!this.#voicesOutstanding.delete(voiceId)) return;
        this.#voicesPlaying.delete(voiceId);
        this.#changed();
    }

    receiveAnswerCount(frame: AnswerCountFrame) {
        this.#answerCount = { answered: frame.answered, players: frame.players };
        if (this.#ownAnswer === OwnAnswer.SENT && frame.own_answer_counted === true)
            this.#ownAnswer = OwnAnswer.COUNTED;
        this.#changed();
    }

    receiveRefusal(frame: RefusedFrame) {
        this.#awaitingAnswer = false;
        if (frame.reason === GAME_REFUSAL.ANSWER_CLOSED) this.#ownAnswer = OwnAnswer.NONE;
        if (frame.reason === GAME_REFUSAL.ANSWER_TOO_LONG) this.#ownAnswer = this.#ownAnswerBeforeLastSend;
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
        this.#ownAnswerBeforeLastSend = this.#ownAnswer;
        // A new answer replaces a counted one without changing the count, so the mark and the count stay.
        if (this.#ownAnswer === OwnAnswer.NONE) this.#ownAnswer = OwnAnswer.SENT;
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

    #questionProgress(quizRound: QuizRound) {
        return words["game.questionProgress"]({ round: String(quizRound.round), rounds: String(quizRound.rounds) });
    }

    #answerStatus() {
        // Until the count that follows the send arrives, the count shown would leave the own answer out.
        if (this.#answerCount === null || this.#ownAnswer === OwnAnswer.SENT) return "";
        const counts = { answered: String(this.#answerCount.answered), players: String(this.#answerCount.players) };
        return this.#ownAnswer === OwnAnswer.COUNTED
            ? words["game.answerSentCountStatus"](counts)
            : words["game.answerCountStatus"](counts);
    }

    #scoreText() {
        return this.#scores.length === 0 ? "" : words["game.scoreLine"]({ scores: scoreEntries(this.#scores) });
    }

    #openCard(quizRound: QuizRound, question: { text: string }): QuestionCardProps {
        this.#answerCount = null;
        this.#ownAnswer = OwnAnswer.NONE;
        this.#ownAnswerBeforeLastSend = OwnAnswer.NONE;
        const card = $state<QuestionCardProps>({
            tag: this.#questionProgress(quizRound),
            question: question.text,
            canReveal: quizRound.starter === this.#ownParticipantId,
            revealDisabled: true,
            locked: true,
            status: "",
            score: "",
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
        card.score = this.#scoreText();
        // The mark waits for the count that includes the own answer: the status line never shows the mark alone.
        card.sent = this.#ownAnswer === OwnAnswer.COUNTED;
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
