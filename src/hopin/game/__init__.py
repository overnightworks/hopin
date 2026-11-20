"""The AI game: a quiz the game master asks the whole call, optional. `main` switches it in; the call core runs without
it and the game has no participation in it.

One quiz per call, its rules in `quiz.py`; the table (`table.py`) runs a room's quizzes one at a time, so a new run
never overlaps the one before it. The connector's game files (`hopin.agent`) give the table its game master.
"""
