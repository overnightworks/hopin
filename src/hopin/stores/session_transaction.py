from contextlib import nullcontext
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webauth.ports import SessionRecordStore, SessionTransaction


def session_transaction_of(sessions: SessionRecordStore) -> SessionTransaction:
    """The one transaction every session read opens; the in-memory store commits each call itself."""
    return lambda: nullcontext(sessions)
