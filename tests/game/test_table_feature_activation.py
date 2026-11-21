from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.config.constants import CALL_FEATURES_PATH
from hopin.config.settings import RealmSettings, ServerSettings
from hopin.game.vocabulary import FEATURE_NAME
from hopin.main import serve
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN
from tests.support.signed_in import cookie_header, seed_member
from tests.support.stores import single_process_stores

if TYPE_CHECKING:
    from fastapi import FastAPI

pytestmark = pytest.mark.integration


def test_the_served_app_offers_a_member_the_game_as_the_calls_feature(
    sign_in_settings, monkeypatch, game_environment, logging_restored
):
    for name, value in sign_in_settings().environment.items():
        monkeypatch.setenv(name, value)
    stores = single_process_stores()

    served: list[FastAPI] = []
    serve(
        ServerSettings(),
        RealmSettings(),
        fixture_page_build(),
        in_thread_stores(),
        stores=stores,
        run=lambda application, _options: served.append(application),
    )
    (served_app,) = served
    member = seed_member(stores, installed_web_auth_config(served_app))
    member_browser = TestClient(
        served_app, base_url=TEST_PUBLIC_ORIGIN, headers={"cookie": cookie_header(member.set_cookie_headers)}
    )

    assert FEATURE_NAME in member_browser.get(CALL_FEATURES_PATH).json()["features"]
