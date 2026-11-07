import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.config.constants import CALL_FEATURES_PATH
from hopin.web.call_features import create_call_features_router
from tests.support.signaling_fakes import RecordingFeature

pytestmark = pytest.mark.integration


def client_for(*features: RecordingFeature) -> TestClient:
    app = FastAPI()
    app.include_router(create_call_features_router(features))
    return TestClient(app)


def test_the_route_answers_no_features_when_the_call_carries_none():
    assert client_for().get(CALL_FEATURES_PATH).json() == {"features": []}


def test_the_route_answers_the_name_of_each_of_the_calls_features_in_order():
    features = [RecordingFeature(rooms=None, name=name) for name in ("sample", "other")]

    assert client_for(*features).get(CALL_FEATURES_PATH).json() == {"features": ["sample", "other"]}


@pytest.mark.parametrize("method", ["get", "head"])
def test_the_route_answers_ok_to_get_and_head(method):
    assert getattr(client_for(), method)(CALL_FEATURES_PATH).status_code == 200
