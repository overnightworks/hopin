"""The game master's surroundings in one place: a fake issuer, a fake agent backend, the stores and a starter."""

import asyncio
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from agents.config import AgentConfig, ChatGateway
from starlette.requests import HTTPConnection
from webauth.admission import AppRole
from webauth.languages import AppLanguages
from webauth.ports import AccountKind

from hopin.agent.backend import QuizGameMaster, StarterPerson
from hopin.agent.settings import AgentSettings
from hopin.auth.sign_in import build_relying_party, build_sign_in_configs
from hopin.game.quiz import QUIZ_TOPICS, Speaker, Turn, question_request
from hopin.stores.session_transaction import session_transaction_of
from hopin.web.page_build import APP_LANGUAGES
from hopin.web.request_language import RequestLanguage
from tests.game.support.fake_agent_backend import FakeAgentBackend
from tests.support.fake_issuer import FakeIssuer
from tests.support.in_thread_stores import in_thread_stores
from tests.support.signed_in import cookie_header, seed_member
from tests.support.stores import single_process_stores

if TYPE_CHECKING:
    from pathlib import Path

    from webauth.single_process_stores import SingleProcessSessionStore, SingleProcessStores

    from tests.support.sign_in_settings import SignInSettings

WAIT_CAP_SECONDS = 10
API_GROUP = "/services/scenario/api"
# A quiz's first run: it asks for the first question.
OPENING = (Turn(Speaker.HOPIN, question_request(QUIZ_TOPICS[0])),)
# An app offering a second language, so a starter's own language differs from the default.
OFFERING_GERMAN_AND_ENGLISH = AppLanguages(offered=("de", "en"), default="de")


class StoreSpy:
    """The session store, signalling each stored refresh token, so a test can wait for a worker's write."""

    def __init__(self, sessions: SingleProcessSessionStore) -> None:
        self._sessions = sessions
        self.stored_refresh_token = threading.Event()

    def __getattr__(self, name):
        return getattr(self._sessions, name)

    def replace_refresh_token(self, session_id, *, refresh_token):
        stored = self._sessions.replace_refresh_token(session_id, refresh_token=refresh_token)
        self.stored_refresh_token.set()
        return stored


@dataclass
class World:
    issuer: FakeIssuer
    backend: FakeAgentBackend
    stores: SingleProcessStores
    sessions: StoreSpy
    relying_party: object
    web_auth: object
    languages: AppLanguages

    @property
    def game_master(self):
        return QuizGameMaster(self.backend, self.starter_person)

    @property
    def starter_person(self):
        return StarterPerson(
            self.relying_party, session_transaction_of(self.sessions), self.web_auth, self.request_language
        )

    @property
    def request_language(self):
        return RequestLanguage(self.languages, session_transaction_of(self.sessions), self.web_auth)

    def starter(self, name="anna", *, with_refresh_token=True, role=None, account_language=None):
        """A socket's connection carrying the session cookie of a member, whose account has `account_language` as
        its language tag, and the member."""
        account = self.stores.accounts.create_for_subject(
            f"subject-{name}", name, role=role or AppRole.USER, kind=AccountKind.PERSON
        )
        refresh_token = self.issuer.refresh_token_for(account.id) if with_refresh_token else None
        member = seed_member(
            self.stores, self.web_auth, account=account, refresh_token=refresh_token, account_language=account_language
        )
        return connection_with(cookie_header(member.set_cookie_headers)), member

    def grant_types(self):
        return [request.form["grant_type"] for request, _ in self.issuer.exchanges]


def connection_with(cookie):
    headers = [] if cookie is None else [(b"cookie", cookie.encode())]
    return HTTPConnection({"type": "websocket", "headers": headers})


def build_world(sign_in: SignInSettings, languages: AppLanguages = APP_LANGUAGES) -> World:
    issuer = FakeIssuer(sign_in)
    configs = build_sign_in_configs(sign_in.settings, (), in_thread_stores().rate_limits)
    stores = single_process_stores()
    sessions = StoreSpy(stores.sessions)
    relying_party = build_relying_party(configs, issuer.transports)
    return World(issuer, FakeAgentBackend(), stores, sessions, relying_party, configs.web_auth, languages)


async def wait_in_a_worker(event):
    assert await asyncio.to_thread(event.wait, WAIT_CAP_SECONDS)


async def cancelled(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def agent_settings(key_file: Path) -> AgentSettings:
    gateway = ChatGateway(origin="http://gateway.test:4000", key_file=key_file)
    return AgentSettings(agent=AgentConfig(gateway=gateway, api_group=API_GROUP))
