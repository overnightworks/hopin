from functools import partial
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.game.vocabulary import GameSignal
from hopin.main import main
from hopin.web.wire import WIRE_MODULE_PATH
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member
from tests.support.wire_module import served_exports

pytestmark = pytest.mark.integration


@patch("hopin.main.uvicorn.run")
def test_the_app_main_starts_carries_the_game_in_its_wire_module(mock_run, sign_in_settings, monkeypatch):
    for name, value in sign_in_settings().environment.items():
        monkeypatch.setenv(name, value)
    stores = SignInStores.in_memory()
    with patch("hopin.main.create_app", partial(create_app, stores=stores)):
        main(["serve"])
    served_app = mock_run.call_args[0][0]
    member = seed_member(stores, installed_web_auth_config(served_app))
    member_browser = TestClient(
        served_app, base_url=TEST_PUBLIC_ORIGIN, headers={"cookie": cookie_header(member.set_cookie_headers)}
    )

    exports = served_exports(member_browser.get(WIRE_MODULE_PATH).text)

    assert GameSignal.GAME_START.name in exports["SIGNAL_TYPE"]
    assert "GAME_PHASE" in exports
