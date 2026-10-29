import json
from typing import TYPE_CHECKING, Any

from hopin.signaling.frames import SEQUENCE_KEY

if TYPE_CHECKING:
    from playwright.sync_api import Page, WebSocketRoute

# The scenarios' own reconnect schedule: the status line pins its first entry on the page,
# the rest only sets how far a scenario moves the page's clock.
RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 8000, 6000)

RECORD_SOCKETS = """
window.openedSockets = [];
const PageWebSocket = window.WebSocket;
window.WebSocket = class extends PageWebSocket {
    constructor(...socketArguments) {
        super(...socketArguments);
        window.openedSockets.push(this);
    }
};
"""
LATE_EVENTS_OF_THE_FIRST_SOCKET = """
() => {
    const [first] = window.openedSockets;
    first.dispatchEvent(new Event("error"));
    first.dispatchEvent(new Event("close"));
}
"""
# Counts every write to the status line, so a scenario can tell a rewrite of the same text from none.
COUNT_STATUS_WRITES = """
window.statusWrites = 0;
new MutationObserver((records) => {
    for (const { target } of records) if (target.id === "status") window.statusWrites++;
}).observe(document, { childList: true, subtree: true });
"""
STATUS_WRITES = "() => window.statusWrites"
STATUS_WRITTEN_AGAIN = "(before) => window.statusWrites > before"


class FrameInjector:
    """Relays a person's signaling socket and sends the server frames the page has no control for.

    The server drops a frame whose sequence number it has already seen, so an injected frame takes
    the next number and the page's later frames move up by one.
    """

    def __init__(self, person: Page) -> None:
        self._server_side: WebSocketRoute | None = None
        self._last_sequence = 0
        self._injected = 0
        person.route_web_socket("**/ws", self._relay)

    def send(self, frame: dict[str, Any]) -> None:
        assert self._server_side is not None
        self._last_sequence += 1
        self._injected += 1
        self._server_side.send(json.dumps({**frame, SEQUENCE_KEY: self._last_sequence}))

    def _relay(self, browser_side: WebSocketRoute) -> None:
        server_side = browser_side.connect_to_server()
        self._server_side = server_side
        browser_side.on_message(lambda frame: server_side.send(self._renumbered(frame)))
        server_side.on_message(browser_side.send)

    def _renumbered(self, frame: str | bytes) -> str | bytes:
        message = json.loads(frame)
        if SEQUENCE_KEY not in message:
            return frame
        self._last_sequence = message[SEQUENCE_KEY] + self._injected
        return json.dumps({**message, SEQUENCE_KEY: self._last_sequence})
