import hmac
import secrets
from typing import Final

REFERENCE_HEX_DIGITS: Final = 16
# The key lives in this process alone and rooms end with it, so a reference names a room for as long as the room exists.
_KEY: Final = secrets.token_bytes(32)


def room_reference(room_id: str) -> str:
    """What a log line says instead of the room id, which is the invite link's token: a keyed digest of the id, so a
    reader of the logs cannot turn it back into the id, and the same room has the same reference in every line."""
    return hmac.digest(_KEY, room_id.encode(), "sha256").hex()[:REFERENCE_HEX_DIGITS]
