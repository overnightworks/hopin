import json
import secrets

import pytest
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import WEBSOCKET_PATH, SignalType
from hopin.game import GameSettings, game_feature
from hopin.game.vocabulary import GameSignal
from tests.game.support.fake_agent_service import FakeAgentService, Succeeds
from tests.support.fake_issuer import FakeIssuer
from tests.support.page_build import fixture_page_build
from tests.support.people_export import ask, read_archive, withheld_counts
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import handshake_headers, seed_member

pytestmark = pytest.mark.integration


@pytest.fixture
def stores():
    return SignInStores.in_memory()


@pytest.fixture
def quiz_call(sign_in_settings, stores):
    """A HopIn whose issuer and agent service are fakes, the service answering the first run with `question`."""
    sign_in = sign_in_settings()
    issuer = FakeIssuer(sign_in)
    service = FakeAgentService(issuer)
    question = f"Quizfrage {secrets.token_hex(8)}?"
    service.answer_next(Succeeds(question))
    app = create_app(
        sign_in.settings,
        page_build=fixture_page_build(),
        issuer_transports=issuer.transports,
        stores=stores,
        call_feature=game_feature(GameSettings(), transport=service.transport),
    )
    with TestClient(app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False) as client:
        yield client, issuer, installed_web_auth_config(app), question


class TestExportAfterAGame:
    def test_holds_no_game_text_and_counts_the_game_frames_among_the_replay_frames(self, quiz_call, stores):
        client, issuer, config, question = quiz_call
        anna_account = stores.accounts.create_for_subject("anna", "anna", config.user_role)
        anna = seed_member(stores, config, account=anna_account, refresh_token=issuer.refresh_token_for("anna"))
        olga = seed_member(
            stores, config, account=stores.accounts.create_for_subject("olga", "olga", config.admin_role)
        )
        with client.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(anna)) as socket:
            socket.send_json({"type": SignalType.JOIN.value})
            socket.receive_json()
            socket.send_json({"type": GameSignal.GAME_START.value, "seq": 1})
            game_frames = [socket.receive_json() for _ in range(3)]

            answer = ask(client, "anna", olga)

        _, export = read_archive(answer.content)
        assert [frame["type"] for frame in game_frames] == ["game_phase", "game_line", "game_phase"]
        assert game_frames[1]["text"] == question
        assert question not in json.dumps(export, ensure_ascii=False)
        assert withheld_counts(export)["call_replay_frame"] == 1 + len(game_frames)
