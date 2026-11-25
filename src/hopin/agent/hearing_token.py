"""The token HopIn holds for an agent with an account of its own, exchanged for the scopes of the service it hears
through, inside the agent's own admitted requests."""

import logging
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Final

from logline import StepTimer
from webauth.middleware import exchanged_access_token
from webauth.oidc import OidcRefusal

from hopin.agent.issuer_error import refusal_reason
from hopin.participation.participants import Person
from hopin.signaling.scheduler import DeadlineKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr
    from starlette.requests import HTTPConnection
    from webauth.oidc import ExchangedAccess

    from hopin.signaling.scheduler import Deadlines, Scheduler

_STEPS: Final = logging.getLogger(__name__)


async def exchange_step(
    connection: HTTPConnection, scopes: tuple[str, ...], wall_clock: Callable[[], float]
) -> ExchangedAccess | OidcRefusal:
    """The token an RFC 8693 exchange of the token the request presented yields for the scopes, under the
    `token_exchange.ended` step; a refusal ends the step `refused` with the issuer's closed error code."""
    async with StepTimer("token_exchange.ended", _STEPS) as step:
        asked_at = int(wall_clock())
        exchanged = await exchanged_access_token(connection, scopes=scopes)
        if isinstance(exchanged, OidcRefusal):
            step.refuse(refusal_reason(exchanged))
        else:
            step.add(expires_in=exchanged.expires_at - asked_at)
    return exchanged


@dataclass(frozen=True)
class _Holding:
    access_token: SecretStr = field(repr=False)
    renews_at: float
    lapses_at: float


class HearingTokens:
    """Holds at most one exchanged token per agent, in memory only, until it expires.

    An exchange needs the token of an admitted call, and a call of a live agent comes at least once per lease. A held
    token is renewed by the first call within a lease of its expiry, or within the second half of its life when the
    issuer granted it for less than two leases, so each token is renewed once, however short its life. A call that
    finds a valid token held is served at once and its renewal runs after it on the scheduler; only a call that finds
    none waits on the exchange. The deadlines drop a token at its expiry, whether or not the agent calls again. A
    refusal is not retried for a lease, and at most one exchange per agent is in flight. With no scopes nothing is
    exchanged. Times run on the deadlines' clock, and the wall clock only turns the issuer's expiry into seconds from
    now."""

    def __init__(
        self,
        scopes: tuple[str, ...],
        *,
        lease_seconds: float,
        deadlines: Deadlines,
        scheduler: Scheduler,
        wall_clock: Callable[[], float],
    ) -> None:
        self._scopes = scopes
        self._lease_seconds = lease_seconds
        self._deadlines = deadlines
        deadlines.register(DeadlineKind.HEARING_LEASE, self._release)
        self._scheduler = scheduler
        self._wall_clock = wall_clock
        self._holdings: dict[Person, _Holding] = {}
        self._refused_until: dict[Person, float] = {}
        self._in_flight: set[Person] = set()

    def held(self, person: Person) -> SecretStr | None:
        """The token exchanged for the agent, or none when it holds nothing or the token has expired."""
        holding = self._holdings.get(person)
        if holding is None or holding.lapses_at <= self._deadlines.now():
            return None
        return holding.access_token

    async def keep(self, person: Person, connection: HTTPConnection) -> None:
        """Exchange the token the agent's admitted request presents when due, after the request when a valid token is
        held; a refusal is logged, not raised."""
        if not self._scopes:
            return
        self._forget_lapsed_refusals()
        if not self._is_due(person):
            return
        self._in_flight.add(person)
        if self.held(person) is None:
            await self._exchange(person, connection)
        else:
            self._scheduler.call_later(0, partial(self._exchange, person, connection))

    async def _exchange(self, person: Person, connection: HTTPConnection) -> None:
        try:
            exchanged = await exchange_step(connection, self._scopes, self._wall_clock)
        finally:
            self._in_flight.discard(person)
        if isinstance(exchanged, OidcRefusal):
            self._refused_until[person] = self._deadlines.now() + self._lease_seconds
            return
        self._hold(person, exchanged.access_token, max(0.0, exchanged.expires_at - self._wall_clock()))

    def _hold(self, person: Person, access_token: SecretStr, seconds_left: float) -> None:
        self._deadlines.set(DeadlineKind.HEARING_LEASE, person.key, seconds_left)
        now = self._deadlines.now()
        renewal_window = min(self._lease_seconds, seconds_left / 2)
        self._holdings[person] = _Holding(access_token, now + seconds_left - renewal_window, now + seconds_left)

    async def _release(self, key: str) -> None:  # NOSONAR(S7503) the deadlines await it
        self._holdings.pop(Person.from_key(key), None)

    def _is_due(self, person: Person) -> bool:
        if person in self._in_flight or person in self._refused_until:
            return False
        holding = self._holdings.get(person)
        return holding is None or holding.renews_at <= self._deadlines.now()

    def _forget_lapsed_refusals(self) -> None:
        now = self._deadlines.now()
        self._refused_until = {person: until for person, until in self._refused_until.items() if until > now}
