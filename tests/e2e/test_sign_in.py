from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, expect
from starlette.requests import Request
from webauth.dependencies import admit_session, session_id_from_request
from webauth.ports import AuthenticatedUser

from tests.e2e.conftest import (
    COUNT_SOCKETS,
    DESKTOP,
    ICE_SERVERS_URL_PATTERN,
    LIVE_TRACK_COUNT,
    OTHER_TILE,
    PHONE,
    PREJOIN_START,
    RECORD_MEDIA_TRACKS,
    RECORD_PEER_CONNECTIONS,
    SHARE_THE_SCREEN_FROM_A_CANVAS,
    RunningServer,
    add_init_scripts,
    join,
    join_by_link,
    open_host,
    open_prejoin,
    poll_until,
    record_page_errors,
    share_the_screen,
    start_call,
    wait_for_peer_count,
    wait_until_the_prejoin_media_answered,
)
from tests.sign_in_settings import TEST_ISSUER

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.signed_in import SeededMember

pytestmark = pytest.mark.e2e

FORM_FACTORS = pytest.mark.parametrize("form_factor", [DESKTOP, PHONE], ids=["desktop", "phone"])


def open_home(page: Page, base_url: str) -> str:
    """Open the home page and answer the Cookie header its document request carried."""
    response = page.goto(f"{base_url}/")
    assert response is not None
    return response.request.all_headers()["cookie"]


def admitted_account(server: RunningServer, cookie_header: str) -> AuthenticatedUser:
    request = Request({"type": "http", "headers": [(b"cookie", cookie_header.encode())]})
    session_id = session_id_from_request(request, server.config)
    assert session_id is not None
    admitted = admit_session(session_id, sessions=server.stores.sessions, config=server.config)
    assert isinstance(admitted, AuthenticatedUser)
    return admitted


@FORM_FACTORS
def test_open_person_holds_only_the_session_cookie_of_its_seeded_member(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], SeededMember],
    e2e_server: RunningServer,
    form_factor: dict[str, Any],
) -> None:
    page = open_person(**form_factor)
    cookie_header = open_home(page, e2e_server.base_url)

    held = {cookie["name"]: cookie for cookie in page.context.cookies()}
    config = e2e_server.config
    assert set(held) == {config.session_cookie_name}
    for cookie in held.values():
        assert (cookie["secure"], cookie["path"], cookie["sameSite"]) == (True, "/", "Lax")
        assert cookie["expires"] > config.wall_clock()
    assert held[config.session_cookie_name]["httpOnly"] is True
    assert all(f"{name}={cookie['value']}" in cookie_header for name, cookie in held.items())
    assert admitted_account(e2e_server, cookie_header).id == member_of(page).account.id


def test_two_people_are_two_members_each_admitted_as_itself(
    open_person: Callable[..., Page], member_of: Callable[[Page], SeededMember], e2e_server: RunningServer
) -> None:
    first, second = open_person(**DESKTOP), open_person(**DESKTOP)
    first_cookie_header = open_home(first, e2e_server.base_url)
    second_cookie_header = open_home(second, e2e_server.base_url)

    assert member_of(first).account.id != member_of(second).account.id
    assert admitted_account(e2e_server, first_cookie_header).id == member_of(first).account.id
    assert admitted_account(e2e_server, second_cookie_header).id == member_of(second).account.id


def test_a_person_opened_signed_out_holds_no_cookie(
    open_person: Callable[..., Page], e2e_server: RunningServer
) -> None:
    page = open_person(signed_in=False, **DESKTOP)
    page.goto(f"{e2e_server.base_url}/health")

    assert page.context.cookies() == []


WATCH_THE_STATUS_LINE = """
document.addEventListener("DOMContentLoaded", () => {
    new MutationObserver(() => window.noteStatus(document.getElementById("status").textContent))
        .observe(document.getElementById("status"), { childList: true, characterData: true, subtree: true });
});
"""
RECORD_POLICY_VIOLATIONS = """
window.policyViolations = [];
document.addEventListener("securitypolicyviolation", (event) => window.policyViolations.push(event.violatedDirective));
"""
ALLOWED_FEATURES = """
() => Object.fromEntries(
    ["camera", "microphone", "display-capture", "geolocation"]
        .map((feature) => [feature, document.featurePolicy.allowsFeature(feature)])
)
"""
NATIVE_SCREEN_SHARE_ERROR = """
async () => {
    try {
        (await navigator.mediaDevices.getDisplayMedia({ video: true })).getTracks().forEach((track) => track.stop());
        return "";
    } catch (error) {
        return String(error.message);
    }
}
"""


def record_document_requests(page: Page) -> list[str]:
    documents: list[str] = []
    page.on("request", lambda request: documents.append(request.url) if request.resource_type == "document" else None)
    return documents


def record_document_statuses(page: Page) -> list[int]:
    statuses: list[int] = []
    page.on(
        "response",
        lambda response: statuses.append(response.status) if response.request.resource_type == "document" else None,
    )
    return statuses


def expect_the_sign_in_at_the_issuer(
    documents: list[str], e2e_server: RunningServer, *, opened: str, next_path: str
) -> None:
    assert len(documents) == 3
    opened_document, start, issuer = documents
    assert opened_document == opened
    assert start == f"{e2e_server.base_url}/auth/start?next={next_path}"
    assert issuer.startswith(TEST_ISSUER)
    redirect_uri = parse_qs(urlsplit(issuer).query)["redirect_uri"]
    assert redirect_uri == [f"{e2e_server.base_url.replace('http://', 'https://')}/auth/callback"]


@FORM_FACTORS
def test_a_person_without_a_session_who_opens_a_call_link_is_sent_to_the_issuer_and_never_sees_the_call(
    open_person: Callable[..., Page], e2e_server: RunningServer, form_factor: dict[str, Any], page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    call_link = start_call(host, e2e_server.base_url)
    room_path = urlsplit(call_link).path
    outsider = record_page_errors(open_person(signed_in=False, **form_factor), page_errors)
    sockets = []
    outsider.on("websocket", lambda socket: sockets.append(socket))
    documents = record_document_requests(outsider)
    statuses = record_document_statuses(outsider)

    with pytest.raises(PlaywrightError, match="ERR_NAME_NOT_RESOLVED"):
        outsider.goto(call_link)

    expect_the_sign_in_at_the_issuer(documents, e2e_server, opened=call_link, next_path=room_path.replace("/", "%2F"))
    assert statuses == [302, 303]
    expect(outsider.locator("[data-screen=prejoin]")).to_have_count(0)
    assert sockets == []
    expect(host.locator("#videoGrid .tile")).to_have_count(1)


@FORM_FACTORS
@pytest.mark.parametrize("end_the_session", ["delete_it_in_the_store", "clear_the_cookies"])
def test_a_page_whose_session_ended_goes_through_the_sign_in_when_the_call_cannot_start(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], SeededMember],
    e2e_server: RunningServer,
    form_factor: dict[str, Any],
    end_the_session: str,
    page_errors: list[str],
) -> None:
    page = record_page_errors(open_person(**form_factor), page_errors)
    statuses: list[str] = []
    page.expose_function("noteStatus", lambda text: statuses.append(text))
    page.add_init_script(WATCH_THE_STATUS_LINE)
    open_prejoin(page, e2e_server.base_url)
    wait_until_the_prejoin_media_answered(page)
    documents = record_document_requests(page)
    if end_the_session == "delete_it_in_the_store":
        e2e_server.stores.sessions.delete(member_of(page).session.id)
    else:
        page.context.clear_cookies()

    with page.expect_response(ICE_SERVERS_URL_PATTERN) as ice_servers, page.expect_event("requestfailed"):
        page.click(PREJOIN_START)

    assert ice_servers.value.status == 401
    expect_the_sign_in_at_the_issuer(documents, e2e_server, opened=f"{e2e_server.base_url}/", next_path="%2F")
    assert not any("Could not load ICE servers" in status for status in statuses)


# The issuer's error page has no media devices.
RECORD_THE_MEDIA_WHERE_THERE_IS_ANY = f"if (navigator.mediaDevices) {{ {RECORD_MEDIA_TRACKS} }}"
# beforeunload runs once the page starts to leave, after its own teardown and before any request goes out.
NOTE_THE_STATE_WHEN_THE_PAGE_LEAVES = f"""
window.addEventListener("beforeunload", () => window.noteTheLeaving({{
    liveTracks: ({LIVE_TRACK_COUNT})(),
    peerStates: window.recordedConnections.map((connection) => connection.signalingState),
    socketsOpened: window.socketsOpened,
}}));
"""
# Playwright waits for the navigation that a click starts, and this click is the last thing the test does.
THE_LOCAL_TRACKS_THAT_ARE_LIVE = (
    "() => document.getElementById('localVideo').srcObject.getTracks().filter((t) => t.readyState === 'live').length"
)
CLICK_THE_MUTE_BUTTON_AFTER_THE_CALL_RETURNS = "() => { setTimeout(() => document.getElementById('muteBtn').click()); }"


@FORM_FACTORS
def test_a_call_whose_session_ended_stops_the_media_before_it_goes_through_the_sign_in(
    open_person: Callable[..., Page],
    member_of: Callable[[Page], SeededMember],
    e2e_server: RunningServer,
    form_factor: dict[str, Any],
    page_errors: list[str],
) -> None:
    host, invite_link = open_host(open_person, e2e_server.base_url, page_errors, form_factor)
    guest = open_person(**form_factor)
    leavings: list[dict[str, Any]] = []
    guest.expose_function("noteTheLeaving", lambda state: leavings.append(state))
    guest = record_page_errors(guest, page_errors)
    add_init_scripts(
        guest,
        (
            RECORD_THE_MEDIA_WHERE_THERE_IS_ANY,
            RECORD_PEER_CONNECTIONS,
            COUNT_SOCKETS,
            NOTE_THE_STATE_WHEN_THE_PAGE_LEAVES,
        ),
    )
    join_by_link(guest, invite_link)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)
    documents = record_document_requests(guest)
    sockets_opened_after_the_session_ended: list[Any] = []
    guest.on("websocket", lambda socket: sockets_opened_after_the_session_ended.append(socket))
    e2e_server.stores.sessions.delete(member_of(guest).session.id)

    guest.evaluate(CLICK_THE_MUTE_BUTTON_AFTER_THE_CALL_RETURNS)

    poll_until(guest, lambda: len(documents), lambda seen: seen == 3, "the page went through the sign-in")
    room_path = urlsplit(invite_link).path
    expect_the_sign_in_at_the_issuer(documents, e2e_server, opened=invite_link, next_path=room_path.replace("/", "%2F"))
    assert leavings == [{"liveTracks": 0, "peerStates": ["closed"], "socketsOpened": 1}]
    assert sockets_opened_after_the_session_ended == []
    expect(host.locator(OTHER_TILE)).to_have_count(0)
    expect(host.locator('[data-screen="call"]')).to_be_visible()
    assert host.evaluate(THE_LOCAL_TRACKS_THAT_ARE_LIVE) > 0


def test_a_member_who_follows_a_call_link_from_another_site_lands_on_the_pre_join_without_the_sign_in(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    host = record_page_errors(open_person(**DESKTOP), page_errors)
    call_link = start_call(host, e2e_server.base_url)
    member = record_page_errors(open_person(**DESKTOP), page_errors)
    other_site = "https://chat.test.invalid/"
    member.route(
        other_site,
        lambda route: route.fulfill(content_type="text/html", body=f'<a id="call" href="{call_link}">call</a>'),
    )
    member.goto(other_site)
    documents = record_document_requests(member)

    member.click("#call")

    expect(member.locator("[data-screen=prejoin]")).to_be_visible()
    assert documents == [call_link]


def test_a_call_runs_under_the_security_headers_with_camera_microphone_and_screen_capture_allowed(
    open_person: Callable[..., Page], e2e_server: RunningServer, page_errors: list[str]
) -> None:
    host, invite_link = open_host(
        open_person, e2e_server.base_url, page_errors, DESKTOP, RECORD_POLICY_VIOLATIONS, SHARE_THE_SCREEN_FROM_A_CANVAS
    )
    guest = join(open_person, invite_link, DESKTOP, (RECORD_PEER_CONNECTIONS, RECORD_POLICY_VIOLATIONS), page_errors)
    wait_for_peer_count(host, 1)
    wait_for_peer_count(guest, 1)

    guest.click("#chatBtn")
    guest.fill("#chatInput", "hello")
    guest.click("#chatSend")
    host.click("#chatBtn")
    expect(host.locator("#chatMessages")).to_contain_text("hello")
    share_the_screen(host)
    guest.click("#leaveBtn")

    assert host.evaluate(ALLOWED_FEATURES) == {
        "camera": True,
        "microphone": True,
        "display-capture": True,
        "geolocation": False,
    }
    assert host.evaluate("() => window.policyViolations") == []
    assert guest.evaluate("() => window.policyViolations") == []


def test_the_screen_capture_of_a_page_is_not_refused_by_the_permissions_policy(
    open_person: Callable[..., Page], e2e_server: RunningServer
) -> None:
    page = open_person(**DESKTOP)
    page.goto(f"{e2e_server.base_url}/")

    assert "permissions policy" not in page.evaluate(NATIVE_SCREEN_SHARE_ERROR).lower()
