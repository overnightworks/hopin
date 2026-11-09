import asyncio
import threading
from contextlib import nullcontext
from typing import TYPE_CHECKING
from weakref import WeakValueDictionary

from webauth.dependencies import session_id_from_request
from webauth.oidc_login import AccessTokenRefusal, AccessTokenRefusalReason, current_groups

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore


class SessionGroups:
    """Reads the groups the issuer holds for a signed-in session now, by one refresh grant of that session."""

    def __init__(self, relying_party: OidcRelyingParty, sessions: SessionRecordStore, web_auth: WebAuthConfig) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth
        self._grant_locks: WeakValueDictionary[str, threading.Lock] = WeakValueDictionary()
        self._grant_locks_guard = threading.Lock()

    def session_id_of(self, connection: HTTPConnection) -> str | None:
        return session_id_from_request(connection, self._web_auth)

    async def of_connection(self, connection: HTTPConnection) -> frozenset[str] | AccessTokenRefusal:
        """The groups of the session the connection carries; a connection with none is a sign-in to repeat."""
        session_id = self.session_id_of(connection)
        if session_id is None:
            return AccessTokenRefusal(AccessTokenRefusalReason.SIGN_IN_AGAIN)
        return await self.of_session(session_id)

    async def of_session(self, session_id: str) -> frozenset[str] | AccessTokenRefusal:
        # The grant blocks on the issuer, so it runs in a worker. A cancelled caller cancels the await,
        # not the worker: it still stores the rotated refresh token, and no later step runs.
        groups = await asyncio.to_thread(self._current_groups, session_id)
        return groups if isinstance(groups, AccessTokenRefusal) else frozenset(groups)

    def _current_groups(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        # An issuer revokes a refresh token on use, so a grant that starts before the previous one stored
        # its rotation sends a revoked token and reads as a false sign-in to repeat.
        grant_lock = self._grant_lock_of(session_id)
        with grant_lock:
            return current_groups(
                self._relying_party,
                session_id,
                session_transaction=lambda: nullcontext(self._sessions),
                config=self._web_auth,
            )

    def _grant_lock_of(self, session_id: str) -> threading.Lock:
        with self._grant_locks_guard:
            grant_lock = self._grant_locks.get(session_id)
            if grant_lock is None:
                grant_lock = self._grant_locks[session_id] = threading.Lock()
            return grant_lock
