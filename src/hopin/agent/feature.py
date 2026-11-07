from dataclasses import dataclass, field
from functools import partial
from random import SystemRandom
from typing import TYPE_CHECKING

from hopin.agent.backend import QuizGameMaster, StarterPerson, gateway_backend
from hopin.agent.settings import AgentSettings, SpeechSettings
from hopin.agent.speech import SpeechVoice
from hopin.game.quiz import QUIZ_ROUNDS, QUIZ_TOPICS
from hopin.game.table import Game
from hopin.game.voice import VoiceStore, create_voice_router

if TYPE_CHECKING:
    from fastapi import APIRouter
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
class BuiltGameFeature:
    """What the game adds to the app: its call feature and the route that serves its voices."""

    factory: FeatureFactory
    router: APIRouter


@dataclass(frozen=True)
class GameFeature:
    """The game, built inside `create_app` on the app's own sign-in and scheduler."""

    backend: QuizBackend
    speech: SpeechSettings = field(default_factory=SpeechSettings)

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """The audience scope the speech service's token carries, asked for only when the voice is on."""
        return () if self.speech.audience_scope is None else (self.speech.audience_scope,)

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
    ) -> BuiltGameFeature:
        starter_person = StarterPerson(relying_party, sessions, auth_config)
        voices = VoiceStore()
        factory = partial(
            Game,
            scheduler=scheduler,
            game_master=QuizGameMaster(self.backend, starter_person),
            topic_drawer=draw_quiz_topics,
            voice=self._voice(relying_party, starter_person),
            voices=voices,
        )
        return BuiltGameFeature(factory, create_voice_router(voices))

    def _voice(self, relying_party: OidcRelyingParty, starter_person: StarterPerson) -> SpeechVoice | None:
        speech = self.speech
        if speech.origin is None or speech.audience_scope is None:
            return None
        return SpeechVoice(speech.origin, speech.voice, speech.audience_scope, relying_party, starter_person)


def configured_game_feature() -> GameFeature:
    """The game as the environment configures it, on the gateway."""
    settings = AgentSettings()
    return GameFeature(gateway_backend(settings), settings.speech)
