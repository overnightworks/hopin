"""Participation: who besides the seats' own people is in a call. Agents a person dials in are its first kind; `main`
switches it in, and it names no agent runtime and has no AI in it.

An agent is dialed in by a seat, whichever seat sponsors it (`feature.py`: presence, bounds, the frames), and takes a
place of the call that `rooms` counts with its people, of both kinds. A place (`places.py`) is an open invitation, bound
to the owner's exact seat with an epoch, or a joined tile that holds the agent's chat window (`chat_window.py`: plain
Python that names no agent runtime; `hopin.agent.mcp` maps and serves it). A seat's park, resume or departure signals
the agents it sponsors through the call core's `seat_changed`.

An agent with an account of its own is live while it calls, on a lease of the connector's `LEASE_SECONDS` that each
admitted call renews; the lease's expiry, or a change of owner, ends its places and signals its standing
(`live_agents.py`).

Who may add: a member of the adders group (`HOPIN_AGENT_ADDERS`, `settings.py`) is offered the owner's live agent in
More while the owner's one seat is in the call, and the call's host and each such member are shown that control even
with no agent listed. `offers.py` keeps the seats' memberships and decides the offers and an add's refusals; the add
reads the adder's groups fresh and opens the invitation.

What a joined agent says aloud is queued per agent (`speaking.py`) and kept as a sound (`voice.py`) until it has played;
who may hear a seat is its sponsor's switch (`hearing.py`), told to a port that the connector fills. `figures.py`
holds an agent's figure.
"""
