# Call

What exactly does a HopIn call promise, from the pre-join to the end screens, and which test proves each rule?

## Pre-join

Ruled by the operator:

- CALL-26: Before I turn my camera on, nobody receives any picture from me.
- CALL-27: Camera off releases the camera, so its light goes off; turning it on opens the camera again.
- CALL-28: The pre-join preview starts with camera and microphone on and stays on the device; nothing reaches the server before Join.
- CALL-29: The call starts with what is on at the pre-join, and what is switched off there stays off in the call; the Start or Join button names that state, such as "Mit Kamera und Mikrofon beitreten".
- CALL-30: A guest who opens a call link gets the same pre-join as the host.
- CALL-31: On a desktop, the HopIn brand leads back from the pre-join to the start page.
- CALL-32: The pre-join's own row shows the account's name, and "You" until the name is known.
- CALL-33: The address becomes the call link when the call starts.

## Call and end screens

Ruled by the operator:

- CALL-01: The host is the signed-in account, not a connection. When the host's last connection drops, the call waits 60 seconds for the host and then ends for everyone.
- CALL-02: A call has no password. The host can lock a running call so that nobody new can join, and unlock it again.
- CALL-03: Calls use a STUN server and, when a direct connection fails, a TURN relay. The provider is configuration; no page code names it.
- CALL-04: Only signed-in members get TURN credentials. Each call start asks the provider for a fresh credential that lives 12 hours and is never refreshed.
- CALL-05: The operator switches TURN on or off in HopIn's configuration; off means STUN only. With TURN on and its key missing or unusable, HopIn refuses to start.
- CALL-06: While the host is away (the 60 seconds after the host's connection dropped), members can still join an unlocked call.
- CALL-07: An away host keeps a place, so a newcomer cannot take the last of the six places while the host is away.
- CALL-08: A broken connection between two people reconnects by itself, without anyone leaving or rejoining, and HopIn keeps trying for as long as both are in the call.
- CALL-09: In bigger calls each picture gets softer, so a phone's total video upload stays at about 2 Mbit/s and the sound stays clear.
- CALL-10: When someone's connection to HopIn drops briefly, they keep their place for 30 seconds while the others see their picture frozen; back within that time, nobody sees them leave and join again.
- CALL-11: For a host whose connection drops, the 60 seconds count from the drop.
- CALL-12: People in a call see each other's Keycloak username on the tiles and in chat (not on a strip tile, CALL-55).
- CALL-13: The same account in one call twice (phone and laptop) joins as two people, and stays host while any of its connections is in the call.
- CALL-14: A call link whose call has ended, or an address that never had a call, shows "Dieser Link ist abgelaufen" when someone joins; HopIn keeps no store of ended calls, so both are the same case.
- CALL-15: The start page is `/` and a call is `/c/{id}`; the old addresses `/call` and `/join/{id}` answer not found.
- CALL-16: "Zurück zum Start" opens the start page as a new page; the browser's back button then returns to the call link, which shows the call as it is now.
- CALL-17: When the host ends the call, everyone else sees "Der Anruf ist beendet" and that the host, by name, ended the call for everyone.
- CALL-18: A host who leaves sees "Du hast den Anruf verlassen" with only "Zurück zum Start"; the call goes on for the others for up to 60 seconds.
- CALL-19: "Wieder beitreten" and "Erneut versuchen" open the call link again, as tapping it in a message does; if the call is over, the person sees "Dieser Link ist abgelaufen".
- CALL-20: When a call ends for me, or HopIn refuses to let me in, my camera and microphone turn off at once.
- CALL-21: A person with the camera off shows as a neutral person icon, the same for everyone.
- CALL-22: On a phone, the small own picture can be dragged to any corner; it snaps to the nearest one and stays there for the rest of the call, also when people join or leave.
- CALL-23: Everyone in a call sees, on that person's tile, who has muted the microphone or turned the camera off; people who join later see it too, and I see my own mute mark on my own tile.
- CALL-24: After I stop sharing my screen, my camera comes back only if it was on before.
- CALL-25: Without a camera I still see and hear everyone; when I turn my camera on later, everyone in the call sees it, those who were there before me and those who joined after me.
- CALL-34: Alone in the call, the host sees "Waiting for others" with a way to invite: Copy link, and on a phone Share, which opens the system share menu. No share box stands in the call otherwise.
- CALL-35: HopIn writes the clipboard only when the person presses Copy.
- CALL-36: The call's controls are round buttons floating over the video. On a phone: mic, camera, invite, chat, more, leave in one row at the bottom. On a desktop: mic, camera, screen | invite, chat, more | leave, centred, with Leave as a pill. Screen sharing exists only on a desktop.
- CALL-37: The number on the Chat button disappears when the chat opens.
- CALL-38: The tile of another person who speaks carries a pulsing accent ring and a three-bar wave in the name pill; the mute mark takes the wave's place while muted; on a phone with two people the full tile shows only the wave; a strip tile shows no name pill (CALL-55).
- CALL-39: In a running call, Impressum and Datenschutz are in More, one tap away; the call screen shows no account button.
- CALL-40: Only the host sees Lock call; a locked call shows "Locked" at the top for everyone.
- CALL-41: A toast fades in and out again over 2.4 seconds.
- CALL-42: One account is in at most 8 calls at once, tabs and devices together. The 9th is refused without retrying and shows "Zu viele offene Anrufe" with "Dein Konto ist schon in 8 Anrufen. Verlass einen, um diesem beizutreten."; a reconnect that meets the limit ends the same way.
- CALL-44: A long chat scrolls inside its panel, and the newest line and the composer stay reachable.
- CALL-45: A tab opened before a release still starts a call: it reloads itself when the new build lacks a piece it needs.
- CALL-46: The end screens name the host by the account's name, and say "Der Host" only when that name is empty.
- CALL-47: Agent tiles take none of the call's six places: the stage shows up to six people plus every agent in the call (the quiz master and up to two agents that joined, a person's own or one with an account of its own), at most nine tiles, and the participant count names people only. With eight or nine tiles the grid is two columns of four or five rows on a phone and three of three on a desktop, and the call controls stay uncovered.
- CALL-48: A person's own agent joins only a call in which that person holds exactly one live seat, is bound to that seat, and leaves when that seat leaves for good; while the seat waits to resume (CALL-10) the tile stays and the agent's calls answer that the sponsor is away.
- CALL-49: The agent's sponsor or the call's host removes an agent from its tile; a removal by anyone else is refused, and other guests see no remove button on the tile.
- CALL-50: A dialed-in agent shows under the name its sponsor gave it (1 to 32 characters after trimming, no control character), with an AI badge and its sponsor's name, so an agent named like a person is still told apart; a name that is no name is refused and no tile shows; a strip tile shows none of them (CALL-55).
- CALL-51: An owner's agent is called through the shared agent control of the fleet's calling standard (the agents repository's `docs/calling.md`), in More. A member of the realm group that names who may add an owner's agent, and the call's host, are shown the control, and anyone for whom it lists an agent; with no agent to list it shows its one sentence. The display rests on one read of the person's groups per seat and never waits on a fresh one: the add reads fresh, and a person a stale display shows the control is refused as not allowed. While the owner holds exactly one seat in the call, the control lists the agent as available on each such member's seat and on no other, and an add request from any other seat is refused. The control lists an agent only while the agent listens on its standing: a lease alone lists none, and the listing ends once the agent's last listen has ended and the agents library's allowance has passed. An add or an accept checks the agent's lease, not its listen. The caller's row shows the agent as inviting and offers no call while the invitation is open; no other member is offered the agent meanwhile. A refused add shows as one of the control's reasons, in the control's words: not allowed, owner absent, place full, agent unavailable or try again; it stays until the person calls again or closes More.
- CALL-52: The invitation belongs to the owner's one seat and lasts the calling standard's `INVITATION_SECONDS` (the agents library's `agents.member`); the agent accepts it with its own call and then shows as a tile under its own name with the AI mark and the owner as sponsor, taking none of the call's six places. It ends with the sponsor's or the host's removal, when the owner's seat leaves for good, when the agent stops calling for 120 seconds, or when the call ends; a removal, a withdrawal or an expiry makes an older invitation stale, so an accept after it changes nothing.
- CALL-53: An agent that holds a joined place in the call, a person's own or one with an account of its own, writes a line with `say`, and every seat reads it in the chat under the name its tile shows, with the AI mark, as plain text. A line holds 1 to 600 characters after trimming and no control character, a line break included; an agent writes at most 10 lines in 60 seconds. An empty or longer line, a line over the rate (`bad_line`, `too_fast`) and a line from an agent with no joined place there (`gone`, an invited agent included) are refused to the agent alone, and no seat sees anything. The call keeps no chat history for seats, so a seat that joins later reads none of the earlier lines; the agents joined in the call read it in their windows (CALL-54).
- CALL-54: An agent that holds a joined place in the call, of either kind, reads every chat line said there after its join, as a person does and without being asked, through the resource of the call's window: it lists the resource, is woken through `subscriptions/listen` when a line is added and reads the window. The window holds only the newest lines, within a bound of count and size; each line has a `seq` counted from 1 within the window's `generation`, the person's or agent's name as the call showed it when the line was said, its kind (`person` or `agent`) and, for the agent's own line, `own`. A line is added for exactly the agents joined when it was said; an empty or non-UTF-8 line or name takes no `seq`; evicted lines show as a `starts_at` beyond the last `seq` read plus one. The window is kept in memory only, goes with the agent's place, is read only through the agent's own principal (a person's own agent through its person, an agent with an account of its own through that account), and holds no line of the game. A person's own agent and an agent with an account of its own are read the same way.
- CALL-55: While a view fills the stage, a strip tile at every width shows no name pill, so neither a name, the wave, the mute mark, the AI badge nor the sponsor; an agent's tile keeps its sparkle.
- CALL-56: An agent with a figure from an allowed source shows it idle in its tile's video layer, drawn on the page of each seat, never as a stream; while the figure loads the tile shows its symbol with a ring, any failure (`unsupported`, `refused`, `lost`, `slow`, or a figure that cannot be fetched or loaded) brings back the symbol for good, and the agent's removal or the call's end takes the figure away. A call with no allowed figure source requests no figure code.
