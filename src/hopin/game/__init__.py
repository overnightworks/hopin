"""The AI game: a quiz the game master asks the whole call. `main` switches it in; the call core runs without it."""

from dataclasses import dataclass
from functools import partial
from random import SystemRandom
from typing import TYPE_CHECKING, Final

import httpx2
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.config.settings import OriginUrl  # noqa: TC001 - pydantic resolves the annotation at runtime
from hopin.game.agent_service import AgentServiceGameMaster, AgentTransport, silence_transport_logs
from hopin.game.quiz import QUIZ_ROUNDS, QUIZ_TOPICS
from hopin.game.table import Game
from hopin.game.tokens import StarterTokens

if TYPE_CHECKING:
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from hopin.signaling.features import FeatureFactory
    from hopin.signaling.scheduler import Scheduler

AGENT_SERVICE_URL = "http://agent-service:8080"
# No retries: a run is started once, never sent again behind the person's back.
AGENT_SERVICE_OVER_HTTP: Final[AgentTransport] = httpx2.AsyncHTTPTransport


def draw_quiz_topics() -> tuple[str, ...]:
    """The topics of one quiz, one per round, drawn at random without repetition."""
    return tuple(SystemRandom().sample(QUIZ_TOPICS, QUIZ_ROUNDS))


def agent_service_game_master(
    agent_service_url: str,
    *,
    relying_party: OidcRelyingParty,
    sessions: SessionRecordStore,
    web_auth: WebAuthConfig,
    transport: AgentTransport,
) -> AgentServiceGameMaster:
    """Build the game master; as a side effect, silence the httpx2 and httpcore2 transport loggers
    that could write a token."""
    silence_transport_logs()
    return AgentServiceGameMaster(
        agent_service_url=agent_service_url,
        tokens=StarterTokens(relying_party, sessions, web_auth),
        transport=transport,
    )


class GameSettings(BaseSettings):
    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    agent_service_url: OriginUrl = AGENT_SERVICE_URL


@dataclass(frozen=True)
class GameFeature:
    """The game, built inside `create_app` on the app's own sign-in and scheduler."""

    settings: GameSettings
    transport: AgentTransport

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
    ) -> FeatureFactory:
        game_master = agent_service_game_master(
            self.settings.agent_service_url,
            relying_party=relying_party,
            sessions=sessions,
            web_auth=auth_config,
            transport=self.transport,
        )
        return partial(Game, scheduler=scheduler, game_master=game_master, topic_drawer=draw_quiz_topics)


def game_feature(settings: GameSettings, transport: AgentTransport | None = None) -> GameFeature:
    return GameFeature(settings, AGENT_SERVICE_OVER_HTTP if transport is None else transport)
