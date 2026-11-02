import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import CALL_FEATURES_PATH
from hopin.web.call_features import create_call_features_router
from tests.support.signaling_fakes import RecordingFeature

pytestmark = pytest.mark.integration


def client_for(feature: RecordingFeature | None) -> TestClient:
    app = FastAPI()
    app.include_router(create_call_features_router(feature))
    return TestClient(app)


def test_the_route_answers_no_features_when_the_call_carries_none():
    assert client_for(None).get(CALL_FEATURES_PATH).json() == {"features": []}


def test_the_route_answers_the_name_of_the_calls_feature():
    feature = RecordingFeature(rooms=None, name="sample")

    assert client_for(feature).get(CALL_FEATURES_PATH).json() == {"features": ["sample"]}


@pytest.mark.parametrize("method", ["get", "head"])
def test_the_route_answers_ok_to_get_and_head(method):
    assert getattr(client_for(None), method)(CALL_FEATURES_PATH).status_code == 200
