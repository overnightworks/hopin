"""The configured provider behind the verdict repeatability check, run as `hopin verdict-repeatability`.

It costs real model calls: the turns run on the configured gateway as a throwaway identity that belongs to the
configured API group. Only the agent settings are read, so it runs in the pod with the pod's environment.
"""

from typing import Final

from agents.backend import TurnPerson
from agents.turn import Principal
from starlette.requests import HTTPConnection

from hopin.agent.backend import QuizGameMaster, gateway_backend
from hopin.agent.settings import AgentSettings
from hopin.game.verdict_repeatability import VerdictAgreement, agreements, recorded_cases

IDENTITY: Final = "hopin-verdict-repeatability"
LANGUAGE: Final = "de"


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


async def configured_agreements(runs: int) -> list[VerdictAgreement]:
    settings = AgentSettings()
    game_master = QuizGameMaster(gateway_backend(settings), ThrowawayStarter(settings.agent.api_group))
    try:
        return await agreements(game_master, HTTPConnection({"type": "http", "headers": []}), recorded_cases(), runs)
    finally:
        await game_master.aclose()
