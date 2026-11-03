from dataclasses import dataclass
from functools import partial
from random import SystemRandom
from typing import TYPE_CHECKING

from hopin.agent.backend import QuizGameMaster, StarterPerson, gateway_backend
from hopin.agent.settings import AgentSettings
from hopin.game.quiz import QUIZ_ROUNDS, QUIZ_TOPICS
from hopin.game.table import Game

if TYPE_CHECKING:
    from webauth.config import WebAuthConfig
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

    from hopin.agent.backend import QuizBackend
    from hopin.signaling.features import FeatureFactory
    from hopin.signaling.scheduler import Scheduler


def draw_quiz_topics() -> tuple[str, ...]:
    """The topics of one quiz, one per round, drawn at random without repetition."""
    return tuple(SystemRandom().sample(QUIZ_TOPICS, QUIZ_ROUNDS))


@dataclass(frozen=True)
class GameFeature:
    """The game, built inside `create_app` on the app's own sign-in and scheduler."""

    backend: QuizBackend

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        return ()

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
    ) -> FeatureFactory:
        game_master = QuizGameMaster(self.backend, StarterPerson(relying_party, sessions, auth_config))
        return partial(Game, scheduler=scheduler, game_master=game_master, topic_drawer=draw_quiz_topics)


def configured_game_feature() -> GameFeature:
    """The game as the environment configures it, on the gateway."""
    return GameFeature(gateway_backend(AgentSettings()))
