"""The agent connector: HopIn's quiz agent run through the fleet's LiteLLM gateway, and the surface over which a
person's own agents reach a call. Optional, and in a layer above the game and participation.

- `mcp.py`: the MCP person front, its protected resource and participation composed for `main`.
- `tools.py`: the tools an agent calls, and the one table of which tool serves which kind of agent.
- `speaking.py`: speech rendered to a WAV. It stays when the game is removed.
- `hearing_route.py`: the hearing socket of a seat.

The starter's check (for the game) and the adders check (for participation) both read a signed-in session's current
groups by one refresh grant, through webauth's `SessionGroups`.

For the game: `settings.py` (`AgentSettings`, with the client's `AgentConfig` as `agent`), `backend.py` (the starter's
verified identity and current groups, one quiz run on the client's `GatewayBackend`, turned into a line or a refusal)
and `feature.py` (the game composed on them for `main`). The quiz agent's file is `src/hopin/agents/quiz.md`.
"""
