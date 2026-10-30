from functools import partial
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.stores import SignInStores
from hopin.config.constants import CALL_FEATURES_PATH
from hopin.game.vocabulary import FEATURE_NAME
from hopin.main import main
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member

pytestmark = pytest.mark.integration


@patch("hopin.main.uvicorn.run")
def test_the_app_main_starts_offers_a_member_the_game_as_the_calls_feature(
    mock_run, sign_in_settings, monkeypatch, fixture_build_directory
):
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

    assert member_browser.get(CALL_FEATURES_PATH).json() == {"features": [FEATURE_NAME]}
