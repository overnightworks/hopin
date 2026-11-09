import asyncio
from contextlib import nullcontext
from typing import TYPE_CHECKING

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
        return current_groups(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )
