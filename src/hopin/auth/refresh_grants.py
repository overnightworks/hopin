"""Runs the refresh grants of one session one at a time, so that none spends a token another has rotated."""

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from webauth.oidc_login import AccessTokenRefusal, current_groups, fresh_access_token

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass
class _Turn:
    """A session's queue: the lock hands the turn on in arrival order, `waiting` counts the grants not yet finished."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiting: int = 0


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
        self._turns: dict[str, _Turn] = {}

    async def current_groups(self, session_id: str) -> tuple[str, ...] | AccessTokenRefusal:
        """The group paths the issuer holds for the session's person, from one refresh grant."""
        return await self._in_turn(session_id, self._groups_grant)

    async def fresh_access_token(self, session_id: str) -> SecretStr | AccessTokenRefusal:
        """A new access token for the session's person, from one refresh grant."""
        return await self._in_turn(session_id, self._access_token_grant)

    async def _in_turn[Granted](self, session_id: str, grant: Callable[[str], Granted]) -> Granted:
        turn = self._turns.setdefault(session_id, _Turn())
        turn.waiting += 1
        started = asyncio.Event()
        task = asyncio.create_task(self._granted(turn, started, session_id, grant))
        task.add_done_callback(lambda finished: self._finish(session_id, turn, finished))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Past its start the worker owns the turn and runs on; before it, the grant leaves the queue.
            if not started.is_set():
                task.cancel()
            raise

    async def _granted[Granted](
        self, turn: _Turn, started: asyncio.Event, session_id: str, grant: Callable[[str], Granted]
    ) -> Granted:
        async with turn.lock:
            started.set()
            return await asyncio.to_thread(grant, session_id)

    def _finish(self, session_id: str, turn: _Turn, finished: asyncio.Task[object]) -> None:
        turn.waiting -= 1
        if turn.waiting == 0:
            del self._turns[session_id]
        if not finished.cancelled() and (failure := finished.exception()) is not None:
            # Every failed grant is logged here, because a cancelled caller never reads its outcome.
            logger.error("refresh_grant_failed", failure_type=type(failure).__name__)

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
