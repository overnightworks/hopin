# Game master

What exactly does the game master promise a call?

Ruled by the operator:

- GM-01: Everyone's "Mehr" holds "Quiz starten"; a tap makes the game master ask one question in everyone's chat.
- GM-04: Answers go into their own answer field, never into the chat; the others see only how many have answered until the reveal. An answer is capped at 200 characters after trimming; a longer one is refused as too long to its sender alone.
- GM-05: A quiz has 5 questions, then the game master's final score.
- GM-06: A question is revealed once everyone present has answered, or when the person who started the quiz taps "Auflösen".
- GM-07: Game master lines carry "Quizmaster" and a "KI" badge, never a person's name.
- GM-08: Every run of a quiz goes out for the person who started it, on that person's access, never another person's.
- GM-09: The person who started the quiz or the host ends it with "Quiz beenden"; other guests cannot.
- GM-10: While the game master runs, everyone sees "Quizmaster denkt nach …"; a second start is refused to the person who tapped it alone.
- GM-11: "Quiz starten" is visible to everyone; without AI access, a tap tells only that person "Dein Konto hat keinen KI-Zugang."
- GM-12: Without a refresh token, the person sees "Melde dich noch einmal an, um Quizmaster zu nutzen." with the button "Anmelden" and the call goes on.
- GM-13: When the game master is busy the starter sees "Quizmaster ist gerade beschäftigt. Versuch es in N Sekunden noch einmal.", when the person's or the app's AI budget is spent "Dein KI-Budget ist vorerst aufgebraucht.", on any other failure "Quizmaster ist gerade nicht erreichbar."; only the starter sees it, and "Quiz starten" is free again.
- GM-14: A drop of up to 30 seconds does not end the quiz.
- GM-15: When the person who started the quiz leaves the call, the quiz ends for everyone with "Quiz beendet: <name> ist gegangen."
- GM-16: People who join during a quiz play from the next question.
- GM-17: Until the reveal, a person can overwrite their own answer; the reveal starts the moment everyone present has answered, and a later overwrite is refused as closed to its sender alone.
- GM-18: The quiz works with 1 to 6 people.
- GM-19: Only members of the AI API group can start a quiz; HopIn adds no group, admin switch or check of its own.
- GM-20: HopIn holds the refresh token per sign-in session in the session's row in its own Postgres, in plain text, never in a log, and replaces it with the rotated one each grant returns, even when the run is cancelled during the grant; it uses it only for three reads and grants: the starter's groups before a game master run, the starter's access token for the speech service, and the agent adders' group read.
- GM-21: Only game text (system text, questions, answers), people's display names and a fingerprint of the starter's identity (a hash of their issuer and account id that keeps their budget apart) leave the call for the AI; chat, audio and video never do, and no name, mail, `sub` or token goes in the identity field. For the voice (GM-32), the line's text and the starter's access token go to the operator's own speech service, which needs the token to admit the starter; HopIn's own workload token goes with it; nothing else leaves the call for it; the service's audio is served unchanged to the seats that got the line.
- GM-22: Each round takes two AI runs, the reveal and then the next question, so a quiz of 5 questions takes up to 11 runs; HopIn never splits AI text.
- GM-23: A run refused mid-quiz ends the quiz for everyone; only the person who started it sees the reason.
- GM-24: HopIn counts the score from the quiz master's verdicts of the reveals so far (GM-40) and hands the counted score to the AI for the final line, a request that names each player with their counted right answers; the AI never counts. The final line also carries the counted final score (`scores`, `final`), and every page shows it in a line of its own after the AI's final line, so the final score never rests on the AI's prose.
- GM-25: A briefly dropped seat inside its 30-second grace counts toward "everyone present has answered".
- GM-26: The AI names each answer in its reveal line.
- GM-27: Every seat sees "Quiz beendet." when the quiz is ended or a run is refused; on a refused run the starter sees only the reason.
- GM-28: There is no "Quiz beenden" while the game master thinks; the server accepts an end in any phase, and a stuck run ends at its 90-second deadline.
- GM-29: The game can be removed again without breaking the call.
- GM-30: A restarted quiz asks different questions: each question names a topic drawn without repetition.
- GM-31: "Mehr" says under "Quiz starten": "Game answers and names go to the AI service. Chat does not.", linked to the privacy text.
- GM-32: With speech configured, every seat that received a game master line and is still in the call starts that line's voice once, unless the browser refuses playback; a resumed seat does not start a voice it already played.
- GM-33: A line's text and its phase change are sent without waiting for its voice; the run for the next line starts only after the previous line's voice was sent or failed; a page stops a playing voice on `game_ended`.
- GM-34: Without its voice for any reason (among them speech unset, the starter's or HopIn's token refused, the service unreachable, refusing, busy or past VOICE_DEADLINE_SECONDS, an answer malformed, incomplete or over VOICE_MAX_BYTES, a line the client refuses, or a browser that cannot fetch or play it) a line stays text only, the quiz goes on as today, nobody sees a notice, and HopIn logs only an outcome code.
- GM-35: The quiz master's tile shows from "Quiz starten" until the quiz ends and its last voice has played.
- GM-36: A line the server will voice carries `voiced: true`, and every voice it starts ends in exactly one `game_voice` to the line's seats still in the call: with the voice's id and path when it was made, without them when it failed, which shows nothing; after the final line's idle the page keeps the tile until that frame has come and its voice has played, failed to load or been refused.
- GM-37: From the quiz's first voiced line until the quiz master's tile goes (the quiz has ended and its last voice has played), each person can mute and unmute the quiz master's voice in their own browser alone: a muted browser fetches and plays no voice, a playing voice stops at once (the last one too, which ends the tile's wait), unmuting plays from the next line on, nothing is stored on the device, a new call starts with the voice on, and the text lines stay the same.
- GM-38: A voice stays fetchable until the speaker's second following voice is made, and is gone with the call, so a seat that was handed a voice never gets it answered as not found while the next line plays; a quiz round with two browsers leaves no failed voice fetch and no console error, every line playing.
- GM-39: The quiz master writes and speaks every line of a quiz in the starter's language: the account's language, else the browser's, else German, decided by the same request language as the page on every run; its prompt names no fixed language, and a language the voice does not speak leaves the line text only (GM-34).
- GM-40: The quiz master returns its verdict on each answer as data with each reveal: through the `record_verdicts` tool, one verdict per answer, naming the answer by its `nr` in the reveal request and judging it `correct` or `wrong`. A verdict that is not that shape, a second call, or verdicts that do not name each given answer exactly once make the run unavailable (the starter sees GM-13's last reason, the quiz ends as in GM-23) and a score is never invented. HopIn counts each player's correct answers from the verdicts, sends the counted score with the reveal line (`scores`), and each page shows it on the next question's card.
- GM-41: The same recorded answers run N times against the configured provider give the same verdicts: `hopin verdict-repeatability --runs N` (the image ships it; `make verdict-repeatability RUNS=N` calls it) reports, for each recorded answer, its most frequent verdict and in how many runs it came, and fails when a share is below `--minimum` (default all runs) or any run gave no verdict, whatever the minimum. It costs real model calls on the configured gateway, so it runs by hand and never in CI, and the server's start never runs it; the recording is `src/hopin/game/verdict_recording.json`, package data. It reads only the agent settings, so it runs in the pod with the pod's environment.
- GM-42: Each run is one chat completion at the fleet's LiteLLM gateway with HopIn's own key as the bearer, which never reaches a log, a frame or a refusal.
- GM-43: A reveal request carries every answer given to that question, numbered and each with the player's name as the call shows it, and lists no player without an answer; every later run of that quiz carries the answers again.
- GM-44: HopIn never logs a prompt, a line or what the gateway answered.
