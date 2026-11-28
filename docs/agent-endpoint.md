# The agent endpoint

This page is for the engineer or operator who connects an agent to HopIn: how an agent reaches a call over MCP, who is admitted and what each tool answers. What an agent may do in a call, as ruled behaviour with its tests, stands in [docs/call.md](call.md); the settings named here are in [docs/configuration.md](configuration.md).

## Who dials in

A person's own agent (first the operator's Claude Code) dials into a call the person sits in, through HopIn's MCP endpoint `POST /mcp` (`src/hopin/agent/mcp.py`, `src/hopin/agent/tools.py` and `src/hopin/participation/`). Everyone in the call then sees a tile with the name the person gave the agent, a "KI" badge and the person's name; the person or the host removes it (CALL-49). The agent joins only a call in which its person holds exactly one live seat, and it leaves when that seat leaves for good or when it calls `leave_call` with the call's id (CALL-48). It has no host or admin right.

An agent with an account of its own is a second kind: a member of the realm group `/agent-adders` adds it from their seat's "Mehr" while the owner holds exactly one seat in the same call (CALL-51, CALL-52). It shows under the name its token carries, never its account's username, and leaves when it is sent away (CALL-84), when its owner's seat leaves for good, or when it has made no call for 120 seconds.

## The bearer

The bearer is accepted on this one path and nowhere else. webauth's gate admits a token whose audience is `HOPIN_PUBLIC_ORIGIN` + `/mcp` alone or exactly that and HopIn's own client id (any other extra audience is refused), the client `REALM_MCP_CLIENT_ID` names, the scope `REALM_MCP_SCOPE` names and membership of the Keycloak group `REALM_MCP_GROUP` names, for a person who has signed in once; the metadata of RFC 9728 is public at `/.well-known/oauth-protected-resource/mcp`. An agent's own account (the group `REALM_AGENT_GROUP` names, any client of the realm, created on its first admission) is admitted as `user` too. The cookie session and its CSRF protection govern every other route, and the bearer opens neither `/api` nor `/ws`.

The disclosure gate binds every agent, because it needs the MCP group too. Until the privacy text names the person's own AI agent and its provider as a recipient at the person's wish, nobody but the operator is put into that group; it is the disclosure gate, and the operator alone controls it.

## The tools

Each tool serves one kind of agent or both; a call to the wrong kind is refused with the reason named below.

| Tool | Serves | What it does |
|---|---|---|
| `join_call(room, name)` | a person's own agent; an own account gets `invitation_only` | Joins the call; a repeat join returns the same agent and keeps the first name |
| `accept_invitation(place, invitation, epoch)` | an own account; a person's own agent gets `own_account_only` | Accepts an invitation of its standing (the runtime's shared arguments, the place's kind `call`) and answers `{room, name}` |
| `decline_invitation(place, invitation, epoch, reason)` | an own account | Declines at once, with the reason `busy`; `stale_invitation` when the invitation is not its own or no longer stands |
| `leave_call(room)` | both | Acts only on a joined place |
| `say(room, text)` | both | Answers `{}`; every seat reads the line under the agent's name with the AI mark, 1 to 600 characters with no control character, else `bad_line` or (no joined place there, an invited agent included) `gone`, for the agent alone; no log line carries the text (CALL-53) |
| `speak(place, text)` | both | Answers `{duration_ms}` and tells every seat the sentence's voice in order (CALL-60), or `{written: true}` when the voice fails and the sentence stands in the chat as the agent's line instead (CALL-61); `not_active`, `too_long`, `unavailable`, `language_unavailable`, `style_unavailable`, `stopped`, `held` and `not_interrupted` go to the agent alone |
| `stop_speaking`, `continue_speaking` | both | Cut or go on with what a person's speaking over the agent held (CALL-60, CALL-69, CALL-70) |
| `gesture`, `set_framing`, `set_turn_state` | both | Show the agent's figure moving, its framing and whether a turn runs (CALL-56, CALL-68, CALL-73, CALL-77); offered while the agent shows a figure |
| `look(place)` | both | One still picture of the person's own camera, only once the person has switched „Agent darf mich sehen“ on its tile, which starts off for every agent and which only the person turns on: one JPEG of at most 1280 px, kept only until it is handed over, never stored or logged, at most 6 looks a minute; the agent's tile shows a mark at each look (CALL-62, CALL-63) |

`join_call` and `accept_invitation` join a call only while it has a place free: agents take places of the call like people (`HOPIN_MAX_PARTICIPANTS_PER_ROOM`, CALL-47), so a join answers `busy` only when the call has no place free.

## What an agent reads

The endpoint serves revision 2026-07-28 only. An agent reads through `resources/read` and wakes on a change through `subscriptions/listen`.

An agent with an account of its own reads the resource `member://standing`: its whole standing, each place it is invited to, active in or paused in, and nothing for one it was removed from. A person's own agent has no standing, and its read is an unknown resource.

Every agent joined in a call, of either kind, reads the call's chat from its join on at the resource that is `place_window_uri` of the place `call/<room>`, listed by `resources/list` for each call where the principal holds a joined agent (next to `member://standing` for an agent with an account of its own) and woken when a line is added (CALL-54). The window holds the newest 100 lines and at most 256 KiB of their text, the agent's own lines marked `own`, and its `voice_state` (`unavailable` after a sentence's voice failed, `available` again at the next sentence the speech service voiced; CALL-61). It is kept in memory only and gone with the agent's place (leave, removal, the sponsor's seat leaving for good, a lease's end, the call's end, shutdown). A person's own agent reads the places of its own person, never those of an agent it sponsors; any other room, an invited agent's room and a room left or ended is an unknown resource. The game's lines are not in the window.
