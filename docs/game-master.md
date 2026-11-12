# Game master

What exactly does the game master promise a call, and which test proves each rule?

Ruled by the operator:

- GM-01: Everyone's "Mehr" holds "Start quiz"; a tap makes the game master ask one question in everyone's chat.
- GM-04: Answers go into their own answer field, never into the chat; the others see only how many have answered until the reveal.
- GM-05: A quiz has 5 questions, then the game master's final score.
- GM-06: A question is revealed once everyone present has answered, or when the person who started the quiz taps Reveal.
- GM-07: Game master lines carry "Game master" and an AI badge, never a person's name.
- GM-08: Every run of a quiz goes out for the person who started it, on that person's access, never another person's.
- GM-09: The person who started the quiz or the host ends it with End quiz; other guests cannot.
- GM-10: While the game master runs, everyone sees "Game master is thinking…"; a second start is refused to the person who tapped it alone.
- GM-11: Start quiz is visible to everyone; without AI access, a tap tells only that person "Your account has no AI access."
- GM-12: Without a refresh token, the person sees "Sign in again to use the game master." and the call goes on.
- GM-13: When the game master is busy the starter sees "Try again in N s", when the person's or the app's AI budget is spent "Your AI budget is used up for now.", on any other failure "The game master is unavailable right now."; only the starter sees it, and Start quiz is free again.
- GM-14: A drop of up to 30 seconds does not end the quiz.
- GM-15: When the person who started the quiz leaves the call, the quiz ends for everyone with "Quiz ended: <name> left."
- GM-16: People who join during a quiz play from the next question.
- GM-17: Until the reveal, a person can overwrite their own answer.
- GM-18: The quiz works with 1 to 6 people.
- GM-19: Only members of the AI API group can start a quiz; HopIn adds no group, admin switch or check of its own.
- GM-20: HopIn holds the refresh token per sign-in session in memory only, never on disk or in a log, and uses it only for the game master.
- GM-21: Only game text (system text, questions, answers), people's display names and a fingerprint of the starter's identity leave the call for the AI; chat, audio and video never do, and no name, mail, `sub` or token goes in the identity field. For the voice (GM-32), the line's text and the starter's access token go to the operator's own speech service, which needs the token to admit the starter; HopIn's own workload token goes with it; nothing else leaves the call for it.
- GM-22: Each round takes two AI runs, the reveal and then the next question, so a quiz of 5 questions takes up to 11 runs; HopIn never splits AI text.
- GM-23: A run refused mid-quiz ends the quiz for everyone; only the person who started it sees the reason.
- GM-24: The AI keeps the score from the quiz so far.
- GM-25: A briefly dropped seat inside its 30-second grace counts toward "everyone present has answered".
- GM-26: The AI names each answer in its reveal line.
- GM-27: Every seat sees "Quiz ended." when the quiz is ended or a run is refused; on a refused run the starter sees only the reason.
- GM-28: There is no End quiz while the game master thinks; the server accepts an end in any phase, and a stuck run ends at its 90-second deadline.
- GM-29: The game can be removed again without breaking the call.
- GM-30: A restarted quiz asks different questions: each question names a topic drawn without repetition.
- GM-31: "Mehr" says under Start quiz: "Game answers and names go to the AI service. Chat does not.", linked to the privacy text.
- GM-32: With speech configured, every seat that received a game master line and is still in the call starts that line's voice once, unless the browser refuses playback; a resumed seat does not start a voice it already played.
- GM-33: A line's text and its phase change are sent without waiting for its voice; the run for the next line starts only after the previous line's voice was sent or failed; a page stops a playing voice on `game_ended`.
- GM-34: Without its voice for any reason (among them speech unset, the starter's or HopIn's token refused, the service unreachable, refusing, busy or past VOICE_DEADLINE_SECONDS, an answer malformed, incomplete or over VOICE_MAX_BYTES, a line the client refuses, or a browser that cannot fetch or play it) a line stays text only, the quiz goes on as today, nobody sees a notice, and HopIn logs only an outcome code.
- GM-35: The quiz master's tile shows from Start quiz until the quiz ends and its last voice has played.
- GM-36: A line the server will voice carries `voiced: true`, and every voice it starts ends in exactly one `game_voice` to the line's seats still in the call: with the voice's id and path when it was made, without them when it failed, which shows nothing; after the final line's idle the page keeps the tile until that frame has come and its voice has played, failed to load or been refused.
- GM-37: From the quiz's first voiced line until the quiz master's tile goes (the quiz has ended and its last voice has played), each person can mute and unmute the quiz master's voice in their own browser alone: a muted browser fetches and plays no voice, a playing voice stops at once (the last one too, which ends the tile's wait), unmuting plays from the next line on, nothing is stored on the device, a new call starts with the voice on, and the text lines stay the same.
