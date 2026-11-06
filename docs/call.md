# Call

What exactly does a HopIn call promise, from the pre-join to the end screens, and which test proves each rule?

## Pre-join

Ruled by the operator:

- CALL-26: Before I turn my camera on, nobody receives any picture from me.
- CALL-27: Camera off releases the camera, so its light goes off; turning it on opens the camera again.
- CALL-28: The pre-join preview starts with camera and microphone on and stays on the device; nothing reaches the server before Join.
- CALL-29: The call starts with what is on at the pre-join, and what is switched off there stays off in the call; the Start or Join button names that state, such as "Join with camera and mic".
- CALL-30: A guest who opens a call link gets the same pre-join as the host.
- CALL-31: On a desktop, the HopIn brand leads back from the pre-join to the start page.
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
- CALL-12: People in a call see each other's Keycloak username on the tiles and in chat.
- CALL-13: The same account in one call twice (phone and laptop) joins as two people, and stays host while any of its connections is in the call.
- CALL-14: A call link whose call has ended shows "This link has expired" when someone joins; HopIn keeps no store of ended calls.
- CALL-15: The start page is `/` and a call is `/c/{id}`; the old addresses `/call` and `/join/{id}` answer not found.
- CALL-16: Back to start opens the start page as a new page; the browser's back button then returns to the call link, which shows the call as it is now.
- CALL-17: When the host ends the call, everyone else sees "Call ended" and "The host ended the call for everyone."
- CALL-18: A host who leaves sees "You left the call" with only "Back to start"; the call goes on for the others for up to 60 seconds.
- CALL-19: Rejoin and Retry open the call link again, as tapping it in a message does; if the call is over, the person sees "This link has expired".
- CALL-20: When a call ends for me, or HopIn refuses to let me in, my camera and microphone turn off at once.
- CALL-21: A person with the camera off shows as a neutral person icon, the same for everyone.
- CALL-22: On a phone, the small own picture can be dragged to any corner; it snaps to the nearest one and stays there for the rest of the call, also when people join or leave.
- CALL-23: Everyone in a call sees, on that person's tile, who has muted the microphone or turned the camera off; people who join later see it too, and I see my own mute mark on my own tile.
- CALL-24: After I stop sharing my screen, my camera comes back only if it was on before.
- CALL-34: Alone in the call, the host sees "Waiting for others" with a way to invite: Copy link, and on a phone Share, which opens the system share menu. No share box stands in the call otherwise.
- CALL-35: HopIn writes the clipboard only when the person presses Copy.
- CALL-36: The call's controls are round buttons floating over the video. On a phone: mic, camera, invite, chat, more, leave in one row at the bottom. On a desktop: mic, camera, screen | invite, chat, more | leave, centred, with Leave as a red pill. Screen sharing exists only on a desktop.
- CALL-37: The number on the Chat button disappears when the chat opens.
- CALL-38: The tile of another person who speaks carries a pulsing accent ring and a three-bar wave in the name pill; the mute mark takes the wave's place while muted; on a phone with two people the full tile shows only the wave.
- CALL-39: In a running call, Impressum and Datenschutz are in More, one tap away; the call screen shows no account button.
- CALL-40: Only the host sees Lock call; a locked call shows "Locked" at the top for everyone.
- CALL-41: A toast fades in and out again over 2.4 seconds.
- CALL-42: One account is in at most 8 calls at once, tabs and devices together. The 9th is refused without retrying and shows "Too many calls open" with "Your account is in 8 calls. Leave one to join this one."; a reconnect that meets the limit ends the same way.
- CALL-44: A long chat scrolls inside its panel, and the newest line and the composer stay reachable.
- CALL-45: A tab opened before a release still starts a call: it reloads itself when the new build lacks a piece it needs.
