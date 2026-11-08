"""The game master's surroundings in one place: a fake issuer, a fake agent backend, the stores and a starter."""

from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

from agent_runtime_client.config import AgentConfig, BackendKind, ChatGateway

from hopin.agent.backend import QuizGameMaster, StarterPerson
from hopin.agent.settings import AgentSettings
from hopin.auth.refresh_grants import SessionGrants
from hopin.game.quiz import QUIZ_TOPICS, Speaker, Turn, question_request
from tests.game.support.fake_agent_backend import FakeAgentBackend
from tests.support.session_world import SessionWorld, build_session_world

if TYPE_CHECKING:
    from pathlib import Path

    from tests.support.sign_in_settings import SignInSettings

API_GROUP = "/services/scenario/api"
# A quiz's first run: it asks for the first question.
OPENING = (Turn(Speaker.HOPIN, question_request(QUIZ_TOPICS[0])),)


@dataclass
class World(SessionWorld):
    backend: FakeAgentBackend = field(default_factory=FakeAgentBackend)

    @cached_property
    def grants(self):
        return SessionGrants(self.relying_party, self.sessions, self.web_auth)

    @property
    def game_master(self):
        return QuizGameMaster(
            self.backend, StarterPerson(self.relying_party, self.sessions, self.web_auth, self.grants)
        )


def build_world(sign_in: SignInSettings) -> World:
    return World(**vars(build_session_world(sign_in)))


def agent_settings(key_file: Path) -> AgentSettings:
    gateway = ChatGateway(origin="http://gateway.test:4000", key_file=key_file)
    return AgentSettings(agent=AgentConfig(backend=BackendKind.LITELLM, gateway=gateway, api_group=API_GROUP))
