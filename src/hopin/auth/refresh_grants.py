"""Runs the refresh grants of one session one at a time, so that none spends a token another has rotated."""

import asyncio
from contextlib import nullcontext
from typing import TYPE_CHECKING

from webauth.oidc_login import AccessTokenRefusal, current_groups, fresh_access_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore


class SessionGrants:
    """Orders webauth's refresh grants per session; webauth still makes each grant and stores its rotated token.

    Where the issuer revokes a refresh token on use, two overlapping grants of one session end in a false
    sign-in-again. A session's turn belongs to the grant's worker, not to its caller, so a cancelled caller
    leaves the turn held until the worker has stored the rotated token. Typed refusals come back as values;
    a store failure raises.
    """

    def __init__(self, relying_party: OidcRelyingParty, sessions: SessionRecordStore, web_auth: WebAuthConfig) -> None:
        self._relying_party = relying_party
        self._sessions = sessions
        self._web_auth = web_auth
        self._latest: dict[str, asyncio.Task[object]] = {}

    async def current_groups(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        """The group paths the issuer holds for the session's person, from one refresh grant."""
        return await self._in_turn(session_id, self._groups_grant)

    async def fresh_access_token(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        """A new access token for the session's person, from one refresh grant."""
        return await self._in_turn(session_id, self._access_token_grant)

    async def _in_turn[Granted](self, session_id: str, grant: Callable[[str], Granted]) -> Granted:
        previous = self._latest.get(session_id)
        started = asyncio.Event()
        task = asyncio.create_task(self._after(previous, started, session_id, grant))
        self._latest[session_id] = task
        task.add_done_callback(lambda finished: self._finish(session_id, finished))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Past its start the worker owns the turn and runs on; before it, nobody waits for this grant.
            if not started.is_set():
                task.cancel()
            raise

    async def _after[Granted](
        self,
        previous: asyncio.Task[object] | None,
        started: asyncio.Event,
        session_id: str,
        grant: Callable[[str], Granted],
    ) -> Granted:
        if previous is not None:
            await asyncio.wait([previous])
        started.set()
        return await asyncio.to_thread(grant, session_id)

    def _finish(self, session_id: str, finished: asyncio.Task[object]) -> None:
        if self._latest.get(session_id) is finished:
            del self._latest[session_id]
        if not finished.cancelled():
            # The caller of a cancelled grant never reads its outcome.
            finished.exception()

    def _groups_grant(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        return current_groups(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )

    def _access_token_grant(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        return fresh_access_token(
            self._relying_party,
            session_id,
            session_transaction=lambda: nullcontext(self._sessions),
            config=self._web_auth,
        )
