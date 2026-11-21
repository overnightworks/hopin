"""The configured provider behind the verdict repeatability check, run as `hopin verdict-repeatability`.

It costs real model calls: the turns run on the configured gateway as a throwaway identity that belongs to the
configured API group. Only the agent settings are read, so it runs in the pod with the pod's environment.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Final

from agents.backend import TurnPerson
from agents.turn import Principal
from starlette.requests import HTTPConnection

from hopin.agent.backend import QuizBackend, QuizGameMaster, gateway_backend
from hopin.agent.settings import AgentSettings
from hopin.game.verdict_repeatability import VerdictAgreement, agreements, recorded_cases

if TYPE_CHECKING:
    import argparse

IDENTITY: Final = "hopin-verdict-repeatability"
LANGUAGE: Final = "de"

type BackendOf = Callable[[AgentSettings], QuizBackend]


def add_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    repeatability = commands.add_parser(
        "verdict-repeatability",
        help="ask the configured quiz master the same recorded reveals again and again and report how often its "
        "verdicts agree; costs real model calls",
    )
    repeatability.add_argument("--runs", type=int, default=10, help="how many times each recorded reveal is asked")
    repeatability.add_argument("--minimum", type=float, default=1.0, help="the least share of runs that must agree")


class ThrowawayStarter:
    """Stands for the starter of a run: a fixed identity of the API group, in German."""

    def __init__(self, api_group: str) -> None:
        self._person = TurnPerson(Principal(iss=IDENTITY, sub=IDENTITY), "user", self._groups)
        self._api_group = api_group

    async def _groups(self) -> frozenset[str]:
        return frozenset({self._api_group})

    async def language_of(self, _starter: HTTPConnection) -> str:
        return LANGUAGE

    async def person_of(self, _starter: HTTPConnection) -> TurnPerson:
        return self._person


async def configured_agreements(runs: int, backend_of: BackendOf = gateway_backend) -> list[VerdictAgreement]:
    settings = AgentSettings()
    game_master = QuizGameMaster(backend_of(settings), ThrowawayStarter(settings.agent.api_group))
    try:
        return await agreements(game_master, HTTPConnection({"type": "http", "headers": []}), recorded_cases(), runs)
    finally:
        await game_master.aclose()
