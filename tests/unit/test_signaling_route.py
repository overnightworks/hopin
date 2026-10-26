from typing import get_type_hints

from starlette.websockets import WebSocket

from hopin.signaling.handler import SignalingHandler


def test_handle_websocket_annotation_resolves_at_runtime() -> None:
    hints = get_type_hints(SignalingHandler.handle_websocket)

    assert hints["websocket"] is WebSocket
