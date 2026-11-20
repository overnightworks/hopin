import pytest
from fastapi.testclient import TestClient

from hopin.app import create_app
from hopin.stage.feature import StageFeature
from hopin.stage.settings import StageApp
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.integration

SKAT = StageApp(name="skat", title="Skat", origin="https://skat.example:8443", frame_path="/t/{context}")
CHESS = StageApp(name="chess", title="Schach", origin="https://chess.example", frame_path="/g/{context}")


def content_security_policy(sign_in_settings, call_features):
    app = create_app(
        sign_in_settings().settings,
        page_build=fixture_page_build(),
        shared_stores=in_thread_stores(),
        call_features=call_features,
    )
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN).get("/health").headers["content-security-policy"]


def test_the_page_may_frame_exactly_the_origin_of_every_registered_app(sign_in_settings):
    policy = content_security_policy(sign_in_settings, (StageFeature((SKAT, CHESS)),))

    assert f"frame-src {SKAT.origin} {CHESS.origin};" in policy


def test_without_apps_the_page_frames_nothing(sign_in_settings):
    assert "frame-src 'none';" in content_security_policy(sign_in_settings, ())
