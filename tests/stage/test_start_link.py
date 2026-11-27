from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.config.constants import CALL_PATH, HEALTH_PATH, HOME_PATH, START_LINK_PATH, WEBSOCKET_PATH, SignalType
from hopin.stage.feature import StageFeature
from hopin.stage.vocabulary import StageSignal
from tests.stage.support.tables import OTHER_TABLE_ID, SKAT, TABLE_ID, TABLE_LINK
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN, TEST_REALM
from tests.support.signaling_fakes import RecordingFeature
from tests.support.signed_in import cookie_header, handshake_headers, seed_member
from tests.support.stores import single_process_stores

pytestmark = pytest.mark.integration

PAGE = fixture_page_build().shell.variants["de"].content
BROWSER_ACCEPT = {"accept": "text/html", "sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
FIXED_FEATURE_PATH = "/api/witness/fixed"
OVERLAPPING_FEATURE_PATH = "/c/{name}"
TEMPLATED_FEATURE_PATH = "/api/witness/{name}"


def start_link(app=SKAT.name, context=TABLE_ID):
    return f"{START_LINK_PATH}?app={app}&context={context}"


class RoutedFeature:
    """A call feature with a fixed route and two with a path parameter, one that a call address also matches."""

    sign_in_scopes: tuple[str, ...] = ()
    frame_sources: tuple[str, ...] = ()

    def __call__(self, **_options):
        router = APIRouter()
        router.add_api_route(FIXED_FEATURE_PATH, lambda: "fixed")
        router.add_api_route(OVERLAPPING_FEATURE_PATH, lambda name: "overlapping")
        router.add_api_route(TEMPLATED_FEATURE_PATH, lambda name: "templated")
        return SimpleNamespace(feature=RecordingFeature(), router=router, bearer_resource=None)


class World:
    """create_app with the Skat stage over stores the test holds, reached as a signed-in browser."""

    def __init__(self, sign_in_settings, call_features):
        self.stores = single_process_stores()
        self.app = create_app(
            sign_in_settings().settings,
            TEST_REALM,
            page_build=fixture_page_build(),
            shared_stores=in_thread_stores(),
            identity=self.stores,
            call_features=call_features,
        )
        config = installed_web_auth_config(self.app)
        self.member = seed_member(self.stores, config)
        self.browser = TestClient(self.app, base_url=TEST_PUBLIC_ORIGIN, follow_redirects=False)
        self.signed_in = TestClient(
            self.app,
            base_url=TEST_PUBLIC_ORIGIN,
            follow_redirects=False,
            headers={"cookie": cookie_header(self.member.set_cookie_headers)},
        )

    @contextmanager
    def call_showing(self, link=TABLE_LINK):
        """A call whose host has attached the table of `link`; the call is open inside the block."""
        with self.signed_in.websocket_connect(WEBSOCKET_PATH, headers=handshake_headers(self.member)) as socket:
            socket.send_json({"type": SignalType.JOIN.value})
            room_id = socket.receive_json()["room_id"]
            socket.send_json({"type": StageSignal.STAGE_ATTACH.value, "link": link, "seq": 1})
            shown = None
            while shown is None:
                frame = socket.receive_json()
                if frame["type"] == StageSignal.STAGE_TABLE.value and frame["attachment"] is not None:
                    shown = frame
            yield room_id


@pytest.fixture
def world(sign_in_settings):
    return World(sign_in_settings, (StageFeature((SKAT,)),))


class TestTheStartLink:
    def test_a_signed_out_navigation_is_sent_through_the_sign_in_and_back_with_its_query(self, world):
        """ST-14."""
        response = world.browser.get(start_link(), headers=BROWSER_ACCEPT)

        assert (response.status_code, response.headers["location"]) == (
            302,
            f"/auth/start?next={quote(start_link(), safe='')}",
        )

    def test_with_no_call_showing_the_table_it_answers_the_page_that_starts_one(self, world):
        """ST-14."""
        response = world.signed_in.get(start_link())

        assert (response.status_code, response.content) == (200, PAGE)

    def test_with_a_call_showing_the_table_it_sends_the_member_to_that_call(self, world):
        """ST-14."""
        with world.call_showing() as room_id:
            response = world.signed_in.get(start_link())

        assert (response.status_code, response.headers["location"]) == (303, f"{CALL_PATH}/{room_id}")

    def test_a_call_showing_another_table_is_not_the_call_of_this_one(self, world):
        """ST-14."""
        with world.call_showing():
            response = world.signed_in.get(start_link(context=OTHER_TABLE_ID))

        assert (response.status_code, response.content) == (200, PAGE)

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "?app=skat",
            f"?context={TABLE_ID}",
            f"?app=chess&context={TABLE_ID}",
            "?app=skat&context=has%20space",
            "?app=skat&context=a%2Fb",
            f"?app=skat&context={'a' * 65}",
            "?app=skat&context=",
            f"?app=skat&app=skat&context={TABLE_ID}",
            f"?app=skat&context={TABLE_ID}&context={TABLE_ID}",
        ],
    )
    def test_a_link_naming_no_registered_table_is_a_404_that_echoes_nothing(self, world, query):
        """ST-14."""
        response = world.signed_in.get(START_LINK_PATH + query)

        assert (response.status_code, response.json()) == (404, {"detail": "Not Found"})


class TestTheRouteOrder:
    @pytest.fixture
    def beside_a_routed_feature(self, sign_in_settings):
        return World(sign_in_settings, (StageFeature((SKAT,)), RoutedFeature()))

    def test_a_feature_with_a_fixed_path_is_answered_ahead_of_the_call_core(self, beside_a_routed_feature):
        """ST-16."""
        assert beside_a_routed_feature.signed_in.get(FIXED_FEATURE_PATH).json() == "fixed"
        assert beside_a_routed_feature.signed_in.get(start_link()).content == PAGE

    def test_a_feature_path_with_a_parameter_comes_after_the_call_core_and_never_takes_its_addresses(
        self, beside_a_routed_feature
    ):
        """ST-16."""
        assert beside_a_routed_feature.signed_in.get(f"{CALL_PATH}/abcd2345").content == PAGE
        assert beside_a_routed_feature.signed_in.get(TEMPLATED_FEATURE_PATH.format(name="x")).json() == "templated"

    def test_the_health_address_stays_the_cores_beside_a_routed_feature(self, beside_a_routed_feature):
        """ST-16."""
        response = beside_a_routed_feature.signed_in.get(HEALTH_PATH)

        assert (response.status_code, response.json()) == (200, {"status": "ok"})

    def test_the_home_address_stays_the_cores_beside_a_routed_feature(self, beside_a_routed_feature):
        """ST-16."""
        response = beside_a_routed_feature.signed_in.get(HOME_PATH)

        assert (response.status_code, response.content) == (200, PAGE)
