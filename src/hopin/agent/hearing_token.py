"""The token HopIn holds for an agent with an account of its own, exchanged for the scopes of the service it hears
through, inside the agent's own admitted requests."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog
from agents.member import LEASE_SECONDS
from webauth.middleware import exchanged_access_token
from webauth.oidc import OidcRefusal

from hopin.agent.backend import issuer_error

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from starlette.requests import HTTPConnection

    from hopin.participation.feature import Person

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


@dataclass(frozen=True)
class _Holding:
    access_token: SecretStr = field(repr=False)
    lapses_at: float


class HearingTokens:
    """Holds at most one exchanged token per agent, in memory only, until it expires.

    A call of a live agent comes at least once per lease, so the token is exchanged again when it holds nothing or what
    it holds lapses within a lease. A refusal is not retried for a lease, and at most one exchange per agent is in
    flight. With no scopes nothing is exchanged. Times run on the scheduler's clock, and the wall clock only turns the
    issuer's expiry into seconds from now."""

    def __init__(self, scopes: tuple[str, ...], *, now: Callable[[], float], wall_clock: Callable[[], float]) -> None:
        self._scopes = scopes
        self._now = now
        self._wall_clock = wall_clock
        self._holdings: dict[Person, _Holding] = {}
        self._refused_until: dict[Person, float] = {}
        self._in_flight: set[Person] = set()

    def held(self, person: Person) -> SecretStr | None:
        """The token exchanged for the agent, or none when it holds nothing or the token has expired."""
        holding = self._holdings.get(person)
        return None if holding is None or holding.lapses_at <= self._now() else holding.access_token

    async def keep(self, person: Person, connection: HTTPConnection) -> None:
        """Exchange the token the agent's admitted request presents when due; a refusal is logged, not raised."""
        if not self._scopes:
            return
        self._forget_lapsed()
        if not self._is_due(person):
            return
        self._in_flight.add(person)
        try:
            exchanged = await exchanged_access_token(connection, scopes=self._scopes)
        finally:
            self._in_flight.discard(person)
        if isinstance(exchanged, OidcRefusal):
            self._refused_until[person] = self._now() + LEASE_SECONDS
            _log_refusal(exchanged)
            return
        seconds_left = max(0.0, exchanged.expires_at - self._wall_clock())
        self._holdings[person] = _Holding(exchanged.access_token, self._now() + seconds_left)

    def _is_due(self, person: Person) -> bool:
        if person in self._in_flight or person in self._refused_until:
            return False
        holding = self._holdings.get(person)
        return holding is None or holding.lapses_at - self._now() <= LEASE_SECONDS

    def _forget_lapsed(self) -> None:
        now = self._now()
        self._holdings = {person: holding for person, holding in self._holdings.items() if holding.lapses_at > now}
        self._refused_until = {person: until for person, until in self._refused_until.items() if until > now}


def _log_refusal(refusal: OidcRefusal) -> None:
    # Only closed codes: a token never reaches a log.
    idp_error = issuer_error(refusal)
    logger.info(
        "agent_hearing_token_refused",
        reason=refusal.reason.value,
        **({} if idp_error is None else {"idp_error": idp_error}),
    )
