# HopIn vision

## The wish

HopIn brings a small circle of people together face to face as easily as opening a link. One person starts a call and shares its link; the others open it in their browser, sign in with their fleet account and are in the call, with nothing to install.

A call should feel like sitting at one table: everyone sees and hears everyone, anyone can show their screen or write beside the video, and the table can play together, with an AI quiz master that asks the questions, judges the answers and keeps the score. For a call of this size it should be as dependable as the big products, so a short drop does not end the conversation.

What is said and shown belongs to the people in the call. Audio and video travel encrypted between their browsers, directly or through a relay that cannot read them; HopIn's server only introduces the browsers to each other and passes on the chat.

HopIn follows open standards instead of inventing its own: the browser's WebRTC carries the call, sign-in is OpenID Connect through the fleet's Keycloak, and the quiz master runs on the fleet's AI gateway.

## Who uses it today

The operator and the accounts he admits to HopIn's group in the fleet's Keycloak. HopIn is made for the friends he invites, who join it as members under terms they accept. No other product calls HopIn.

## Deliberate non-goals

- **No use without an account.** Every call needs a sign-in; a link alone admits nobody, and there are no anonymous guests.
- **No media through the server.** HopIn's server never carries or records audio or video.
- **No large meetings or broadcasts.** Every browser sends to every other, so a call stays small: six people at most.
- **No archive.** HopIn keeps no chat history and no recording; what was said and written is gone when the call ends.
- **No accounts or AI access rules of its own.** The fleet's groups decide who signs in and who may use AI, and the fleet's gateway holds the AI budget.
