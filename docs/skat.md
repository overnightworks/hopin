# Skat table

What does HopIn promise a call about the Skat table on its stage, and which test proves each rule? The game itself, its chairs and its sign-in are Skat's.

The head's decisions, which carry the item's lines O1 A, O2 A and H10:

- SK-01: With `HOPIN_SKAT_ORIGIN` set, anyone in a call taps Show Skat table in More, pastes the link of a table of that origin, and every seat of the call, and everyone who joins later, shows that table on the stage. Proven by `tests/skat/test_feature.py` (the state every seat and a late joiner is sent) and `tests/e2e/skat/test_skat_table.py` (a pasted link on every stage and to a later joiner).
- SK-02: Every video moves into a strip at the top and goes on playing; picture-in-picture is off while the table shows, and a held drag is released. Proven by `tests/e2e/skat/test_skat_table.py` (strip, videos, the held tile) and `frontend/src/lib/ui/pip.test.ts`.
- SK-03: The table is removed by the person who brought it or by the host, and, once the person who brought it has left, by anyone. Proven by `tests/skat/test_feature.py` (`TestDetach`, `TestTheOpenerLeaves`) and `tests/e2e/skat/test_skat_table.py`.
- SK-04: Of two attaches at once one wins and the other is told the stage is taken; a detach of an older table removes nothing newer. Proven by `tests/skat/test_feature.py`.
- SK-05: A seat that joins afresh, after a reload or a refused resume, is sent the room's current state and shows that and nothing older; a resume keeps the frame. Proven by `tests/skat/test_feature.py` (`TestFreshJoinsAndTheCallsEnd`) and `frontend/src/lib/features/skat/table.svelte.test.ts` (frames in any order).
- SK-06: The call's end takes the table away. Proven by `tests/skat/test_stage.py`, `tests/skat/test_feature.py` (`TestTheCallsEnd`) and `tests/e2e/skat/test_skat_table.py`.
- SK-07: Unset, there is no Skat: no item in More and `frame-src 'none'`. Set, the page may frame exactly that origin, and an origin equal to `HOPIN_PUBLIC_ORIGIN` as a browser compares origins refuses the start. Proven by `tests/skat/test_settings.py`, `tests/skat/test_feature_csp.py` and `tests/test_app.py`.
- SK-08: Only a link to a table of the configured origin is accepted, and the frame loads `{origin}/t/{id}?mode=call` alone. Proven by `tests/skat/test_settings_links.py`.
- SK-09: The frame cannot use the camera, the microphone or screen capture, sends no referrer, and cannot navigate the page, open a modal or submit a form. Proven by `tests/e2e/skat/test_skat_table.py` against a stand-in table page.
- SK-10: Removing Skat from the code leaves the call and the other features, and removing the game leaves Skat. Proven by the CI job `Removal drill`, whose legs run the game's and Skat's deletion.

Deferred: the operator's ruling B, each player's video at that player's chair, belongs to the slice 2b of overnightworks/hopin#499; until then every video is in the strip.
