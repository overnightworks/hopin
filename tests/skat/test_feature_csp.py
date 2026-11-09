import pytest
from fastapi.testclient import TestClient

from hopin.app import create_app
from hopin.skat.feature import SkatFeature
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.integration

SKAT_ORIGIN = "https://skat.example:8443"


def content_security_policy(sign_in_settings, call_features):
    app = create_app(sign_in_settings().settings, page_build=fixture_page_build(), call_features=call_features)
    return TestClient(app, base_url=TEST_PUBLIC_ORIGIN).get("/health").headers["content-security-policy"]


def test_the_page_may_frame_exactly_skats_origin(sign_in_settings):
    policy = content_security_policy(sign_in_settings, (SkatFeature(SKAT_ORIGIN),))

    assert f"frame-src {SKAT_ORIGIN};" in policy


def test_without_skat_the_page_frames_nothing(sign_in_settings):
    assert "frame-src 'none';" in content_security_policy(sign_in_settings, ())
