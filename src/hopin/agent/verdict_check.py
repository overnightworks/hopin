"""The configured provider behind the verdict repeatability check, run as `hopin verdict-repeatability`.

It costs real model calls: the turns run on the configured gateway as a throwaway identity that belongs to the
configured API group. Only the agent settings are read, so it runs in the pod with the pod's environment.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from starlette.requests import HTTPConnection

from hopin.agent.backend import QuizBackend, QuizGameMaster, ThrowawayStarter, gateway_backend
from hopin.agent.settings import AgentSettings
from hopin.game.verdict_repeatability import VerdictAgreement, agreements, recorded_cases

if TYPE_CHECKING:
    import argparse

type BackendOf = Callable[[AgentSettings], QuizBackend]


def add_command(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    repeatability = commands.add_parser(
        "verdict-repeatability",
        help="ask the configured quiz master the same recorded reveals again and again and report how often its "
        "verdicts agree; costs real model calls",
    )
    repeatability.add_argument("--runs", type=int, default=10, help="how many times each recorded reveal is asked")
    repeatability.add_argument("--minimum", type=float, default=1.0, help="the least share of runs that must agree")


async def configured_agreements(runs: int, backend_of: BackendOf = gateway_backend) -> list[VerdictAgreement]:
    settings = AgentSettings()
    game_master = QuizGameMaster(backend_of(settings), ThrowawayStarter(settings.agent.api_group))
    try:
        return await agreements(game_master, HTTPConnection({"type": "http", "headers": []}), recorded_cases(), runs)
    finally:
        await game_master.aclose()
