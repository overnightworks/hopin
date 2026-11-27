"""CALL-81 to CALL-83: an agent's video session at HopIn's real `/mcp` and `/ws`, opened on a fake video service on a
loopback port under a fake issuer and a fake TURN provider, and the fall to its figure when the session ends."""

import asyncio
import json
import socket
import threading
import time
from http import HTTPStatus
from types import SimpleNamespace

import pytest
from agents.member import LEASE_SECONDS
from speech_client.wire import VoiceId
from video_client.session_wire import SessionCreated, SessionInfo, SessionUpdate, SessionUpdated, error_event
from video_client.wire import AUTHORIZATION_HEADER, WORKLOAD_TOKEN_HEADER, RefusalCode, caller_headers
from websockets.frames import CloseCode

from hopin.agent import video_session
from hopin.agent.mcp import ParticipationFeature
from hopin.agent.speech_settings import SpeechService
from hopin.agent.tools import LEAVE_CALL, AcceptedVideoLooks
from hopin.agent.video_settings import VideoService
from hopin.participation.figures import FIGURE_FILES
from hopin.participation.participants import Person
from hopin.participation.places import Places
from hopin.participation.video_looks import VideoLooks
from hopin.participation.vocabulary import ParticipationSignal, VideoFailure
from hopin.rooms.reference import room_reference
from tests.participation.support.figure_scene import FILE, JOINED, accept_with, figure_look, read_figure
from tests.participation.support.figures import SOURCE_PREFIX, digest_of, figure_url, ok
from tests.support.agent_speak import SENTENCE_SECONDS, speak
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT
from tests.support.fake_turn import (
    PROVIDER_CREDENTIAL,
    PROVIDER_USERNAME,
    RELAY_URLS,
    FakeTransport,
    provider_answer,
)
from tests.support.fake_video import PICTURE, WAIT_SECONDS, FakeVideo, Refusal, refused_with
from tests.support.log_lines import captured_lines
from tests.support.sign_in_settings import TEST_REALM
from tests.support.signaling_fakes import ManualScheduler
from tests.support.speech_origin import AUDIENCE_SCOPE as SPEECH_SCOPE
from tests.support.speech_origin import header_of, speech_requests

pytestmark = pytest.mark.integration

VIDEO_SCOPE = "video-service-audience"
ENDED_EVENT = "video_session.ended"
CLIENT_CREDENTIALS_GRANT = "client_credentials"
ESTABLISHED = "mozart"
OTHER = "salieri"
FIRST, SECOND = {"kind": "video", "name": ESTABLISHED}, {"kind": "video", "name": OTHER}


@pytest.fixture
def sources():
    return [SOURCE_PREFIX]


@pytest.fixture
def fake_video():
    service = FakeVideo({ESTABLISHED: True, OTHER: True})
    service.start()
    yield service
    service.stop()


@pytest.fixture
def video_service(fake_video):
    return VideoService(fake_video.origin, VIDEO_SCOPE)


class TurnGate:
    """The body of the TURN provider's answer: a clear `issuing` holds it back, and `asked` says a request came."""

    def __init__(self, answer: bytes) -> None:
        self._answer = answer
        self.issuing = threading.Event()
        self.issuing.set()
        self.asked = threading.Event()

    def __call__(self) -> bytes:
        self.asked.set()
        assert self.issuing.wait(WAIT_SECONDS), "a credential request was held for good"
        return self._answer


@pytest.fixture
def turn_gate():
    return TurnGate(json.dumps(provider_answer()).encode())


@pytest.fixture
def turn_provider(turn_gate):
    return FakeTransport(body=turn_gate)


@pytest.fixture
def speaking_service(speech_origin):
    return SpeechService(speech_origin.base_url, VoiceId.THORSTEN, SPEECH_SCOPE)


@pytest.fixture
def lines():
    with captured_lines("websockets", video_session.SOCKET_LOGGER_NAME) as captured:
        yield captured


@pytest.fixture
def open_deadline_seconds(monkeypatch):
    """The open deadline a test that lets it run out shortens, so the test does not wait the service's 10 s."""
    monkeypatch.setattr(video_session, "VIDEO_SESSION_OPEN_SECONDS", 0.3)


def accepted(scene, looks):
    """The member adds the agent and it accepts with the looks; the open starts when the app's scheduler runs."""
    assert not accept_with(scene, None, looks=looks).is_error
    scene.joined = scene.owner.frame_of_type(JOINED)
    scene.call.pass_time(0)


def ended_lines(lines):
    return [line for line in lines if line["event"] == ENDED_EVENT]


def tokens_exchanged_for(call, scope):
    """The access tokens the issuer exchanged for the scope, in order."""
    return [
        json.loads(answer.body)["access_token"]
        for request, answer in call.issuer.exchanges
        if request.form["grant_type"] == TOKEN_EXCHANGE_GRANT and request.form["scope"] == scope and answer is not None
    ]


def exchanges_for(call, scope):
    return [
        request
        for request, _ in call.issuer.exchanges
        if request.form["grant_type"] == TOKEN_EXCHANGE_GRANT and request.form["scope"] == scope
    ]


def figure_is_still_shown(scene):
    """Whether the figure the agent joined with is still served: the seat shows it until the agent leaves."""
    return read_figure(scene, scene.joined["figure"]).status_code == 200


@pytest.fixture
def figure_served(figure_origin):
    figure_origin.serve(figure_url(), ok(FILE))


@pytest.fixture
def with_a_figure_after(scene, figure_served):
    """The looks of an accept that name a video look and then a figure."""
    return [FIRST, figure_look(figure_url())]


class TestAVideoSessionOpens:
    def test_call_81_a_ready_first_video_look_opens_one_session_on_the_looks_name(
        self, scene, fake_video, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)

        session = fake_video.next_session()

        assert (session.look, len(fake_video.sessions)) == (ESTABLISHED, 1)

    def test_call_81_the_session_is_opened_under_the_accepts_token_exchanged_for_the_video_audience_alone(
        self, scene, fake_video, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)

        session = fake_video.next_session()

        [request] = exchanges_for(scene.call, VIDEO_SCOPE)
        [video_token] = tokens_exchanged_for(scene.call, VIDEO_SCOPE)
        assert (request.form["subject_token"], session.header(AUTHORIZATION_HEADER)) == (
            scene.token,
            f"Bearer {video_token}",
        )

    def test_call_81_the_session_is_opened_under_hopins_own_workload_token(
        self, scene, fake_video, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)

        session = fake_video.next_session()

        [workload] = scene.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)
        assert session.header(WORKLOAD_TOKEN_HEADER) == workload

    def test_call_83_the_session_update_names_the_relay_servers_alone_under_the_relay_policy(
        self, scene, fake_video, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)

        update = SessionUpdate.model_validate(fake_video.next_session().wait_for_update())

        assert update.session.ice_transport_policy == "relay"
        [server] = update.session.ice_servers
        assert (server.urls, server.username, server.credential) == (RELAY_URLS, PROVIDER_USERNAME, PROVIDER_CREDENTIAL)

    def test_call_81_the_accept_leads_to_one_exchange_for_the_video_audience_and_each_exchange_asks_one_scope(
        self, scene, fake_video, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)
        fake_video.next_session()

        exchanged = [
            request.form["scope"]
            for request, _ in scene.call.issuer.exchanges
            if request.form["grant_type"] == TOKEN_EXCHANGE_GRANT
        ]
        assert (exchanged.count(VIDEO_SCOPE), all(" " not in scope for scope in exchanged)) == (1, True)

    def test_call_82_the_first_look_that_the_health_offers_ready_opens_when_an_earlier_one_is_not(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        fake_video.looks[ESTABLISHED] = False
        accepted(scene, [FIRST, SECOND, figure_look(figure_url())])

        session = fake_video.next_session()

        assert (session.look, ended_lines(lines)) == (OTHER, [])

    @pytest.mark.parametrize(
        ("fail", "reason"),
        [
            pytest.param(lambda session: session.fail(RefusalCode.UNAVAILABLE), VideoFailure.ERROR, id="an-error"),
            pytest.param(lambda session: session.hang_up(), VideoFailure.CLOSED, id="a-close"),
            pytest.param(
                lambda session: session.say(SessionUpdated().model_dump_json().encode()),
                VideoFailure.MALFORMED,
                id="an-event-in-a-binary-frame",
            ),
            pytest.param(lambda session: session.say("no event"), VideoFailure.MALFORMED, id="a-text-that-is-no-event"),
        ],
    )
    def test_call_82_a_failed_session_mid_call_ends_it_for_good_with_one_line_and_leaves_the_figure(
        self, scene, fake_video, with_a_figure_after, lines, fail, reason
    ):
        accepted(scene, with_a_figure_after)
        session = fake_video.next_session()
        session.wait_for_update()

        fail(session)

        assert lines.wait_for_event(ENDED_EVENT, 10)
        scene.call.pass_time(1)
        [line] = ended_lines(lines)
        assert (line["outcome"], line["reason"], len(fake_video.sessions), figure_is_still_shown(scene)) == (
            "refused",
            reason.value,
            1,
            True,
        )


class TestNoSessionOpens:
    @pytest.mark.parametrize(
        ("looks", "video_look_is_ready"),
        [
            pytest.param([figure_look(figure_url()), FIRST], True, id="a-figure-look-before-a-ready-video-look"),
            pytest.param([FIRST], False, id="a-video-look-the-health-does-not-offer-ready"),
        ],
    )
    def test_call_81_with_no_ready_video_look_ahead_of_its_figure_no_session_opens_and_no_line_is_written(
        self, scene, fake_video, figure_served, lines, looks, video_look_is_ready
    ):
        fake_video.looks[ESTABLISHED] = video_look_is_ready
        accepted(scene, looks)

        assert (fake_video.upgrades, ended_lines(lines), exchanges_for(scene.call, VIDEO_SCOPE)) == (0, [], [])

    def test_call_81_a_look_after_a_not_ready_one_and_a_figure_is_not_shown(
        self, scene, fake_video, figure_served, lines
    ):
        fake_video.looks[ESTABLISHED] = False
        accepted(scene, [FIRST, figure_look(figure_url()), SECOND])

        assert (fake_video.upgrades, ended_lines(lines)) == (0, [])


class TestTurnOff:
    @pytest.fixture
    def turn_provider(self):
        return None

    def test_call_81_with_turn_off_no_session_opens_and_nothing_is_asked_of_the_service(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        accepted(scene, with_a_figure_after)

        assert (fake_video.upgrades, ended_lines(lines), exchanges_for(scene.call, VIDEO_SCOPE)) == (0, [], [])


class TestSpeakingOff:
    @pytest.fixture
    def speaking_service(self):
        return None

    def test_call_81_with_speaking_off_no_session_opens_and_nothing_is_asked_of_the_service(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        accepted(scene, with_a_figure_after)

        assert (fake_video.upgrades, ended_lines(lines), exchanges_for(scene.call, VIDEO_SCOPE)) == (0, [], [])


class TestVideoOff:
    @pytest.fixture
    def video_service(self):
        return None

    def test_call_81_with_the_origin_unset_the_service_is_asked_nothing_and_no_video_token_is_exchanged(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        accepted(scene, with_a_figure_after)

        assert (
            fake_video.health_requests,
            fake_video.upgrades,
            ended_lines(lines),
            exchanges_for(scene.call, VIDEO_SCOPE),
            scene.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT),
        ) == (0, 0, [], [], [])


FAILURES = [
    pytest.param(
        lambda fake_video, issuer, turn: setattr(fake_video, "health_status", HTTPStatus.SERVICE_UNAVAILABLE),
        VideoFailure.HEALTH_UNREAD,
        id="the-health-cannot-be-read",
    ),
    pytest.param(
        lambda fake_video, issuer, turn: setattr(issuer, "refuses_token_exchange_grants", True),
        VideoFailure.TOKEN_EXCHANGE_REFUSED,
        id="the-token-exchange-is-refused",
    ),
    pytest.param(
        lambda fake_video, issuer, turn: setattr(issuer, "refuses_client_credentials_grants", True),
        VideoFailure.WORKLOAD_TOKEN_REFUSED,
        id="the-workload-grant-is-refused",
    ),
    pytest.param(
        lambda fake_video, issuer, turn: setattr(turn, "status", HTTPStatus.INTERNAL_SERVER_ERROR),
        VideoFailure.TURN_CREDENTIAL_UNAVAILABLE,
        id="no-turn-credential-comes",
    ),
    pytest.param(
        lambda fake_video, issuer, turn: setattr(
            fake_video, "refusal", Refusal(HTTPStatus.BAD_GATEWAY, "a refusal that is no wire body")
        ),
        VideoFailure.REFUSED,
        id="the-upgrade-is-refused-without-a-wire-body",
    ),
    *(
        pytest.param(
            lambda fake_video, issuer, turn, code=code: setattr(fake_video, "refusal", refused_with(code)),
            VideoFailure(code.value),
            id=f"the-upgrade-is-refused-{code.value}",
        )
        for code in (
            RefusalCode.UNAUTHORIZED,
            RefusalCode.FORBIDDEN,
            RefusalCode.INVALID_REQUEST,
            RefusalCode.UNAVAILABLE,
            RefusalCode.BUSY,
        )
    ),
]


class TestASessionThatDoesNotOpen:
    @pytest.mark.parametrize(("break_it", "reason"), FAILURES)
    def test_call_82_each_refusal_ends_the_attempt_for_good_with_one_refused_line_and_leaves_the_figure(
        self, scene, fake_video, turn_provider, with_a_figure_after, lines, break_it, reason
    ):
        break_it(fake_video, scene.call.issuer, turn_provider)
        accepted(scene, with_a_figure_after)
        scene.call.pass_time(1)

        [line] = ended_lines(lines)
        assert (
            line["outcome"],
            line["reason"],
            line["room_ref"],
            fake_video.upgrades <= 1,
            figure_is_still_shown(scene),
        ) == ("refused", reason.value, room_reference(scene.owner.room_id), True, True)
        assert len(fake_video.sessions) == 0

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            pytest.param(error_event(RefusalCode.UNAVAILABLE), VideoFailure.ERROR, id="an-error-event"),
            pytest.param(
                SessionCreated(
                    session=SessionInfo(id="again", look=ESTABLISHED, video=PICTURE, max_queued_audio_seconds=2.0)
                ),
                VideoFailure.MALFORMED,
                id="another-event-than-the-update",
            ),
        ],
    )
    def test_call_82_a_service_that_answers_the_update_with_anything_else_ends_the_attempt_and_closes_the_session(
        self, scene, fake_video, with_a_figure_after, lines, answer, reason
    ):
        fake_video.update_reply = answer
        accepted(scene, with_a_figure_after)
        scene.call.pass_time(1)

        [line] = ended_lines(lines)
        assert (line["outcome"], line["reason"], figure_is_still_shown(scene)) == ("refused", reason.value, True)
        fake_video.next_session().wait_until_closed()

    def test_call_82_a_service_that_nothing_listens_on_ends_the_open_as_unreachable(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            origin = f"http://127.0.0.1:{unused.getsockname()[1]}"

        opened = asyncio.run(video_session.open_connection(origin, ESTABLISHED, {}, []))

        assert opened is VideoFailure.UNREACHABLE

    def test_call_82_the_open_that_runs_past_its_deadline_ends_the_attempt_with_one_refused_line(
        self, scene, fake_video, with_a_figure_after, lines, open_deadline_seconds
    ):
        fake_video.updates = False
        accepted(scene, with_a_figure_after)

        [line] = ended_lines(lines)
        assert (line["outcome"], line["reason"], figure_is_still_shown(scene)) == (
            "refused",
            VideoFailure.PAST_DEADLINE.value,
            True,
        )
        fake_video.next_session().wait_until_closed()

    def test_call_82_the_line_carries_the_room_and_the_agent_beyond_the_standard_step_fields(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        fake_video.health_status = HTTPStatus.SERVICE_UNAVAILABLE
        accepted(scene, with_a_figure_after)

        [line] = ended_lines(lines)
        assert (line["agent_id"], line["room_ref"]) == (scene.joined["agent_id"], room_reference(scene.owner.room_id))

    @pytest.mark.parametrize(
        "looks",
        [
            pytest.param(lambda figure: [FIRST, figure, SECOND], id="a-figure-between-the-video-looks"),
            pytest.param(lambda figure: [FIRST, SECOND, figure], id="the-video-looks-before-the-figure"),
        ],
    )
    def test_call_82_a_failed_first_look_opens_no_second_session_and_leaves_the_figure(
        self, scene, fake_video, figure_served, lines, looks
    ):
        fake_video.refusal = refused_with(RefusalCode.UNAVAILABLE)
        accepted(scene, looks(figure_look(figure_url())))
        scene.call.pass_time(1)

        assert (
            fake_video.upgrades,
            fake_video.health_requests,
            len(ended_lines(lines)),
            figure_is_still_shown(scene),
        ) == (
            1,
            1,
            1,
            True,
        )


def leaves(scene):
    scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id})


def is_removed(scene):
    scene.owner.send({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": scene.joined["agent_id"]})


def lease_ends(scene):
    scene.call.pass_time(LEASE_SECONDS)


def call_ends(scene):
    scene.call.end_room_after(scene.owner.room_id, lambda: None)


def shuts_down(scene):
    scene.call.shut_down()


class TestThePlaceEnds:
    @pytest.mark.parametrize(
        "ends",
        [
            pytest.param(leaves, id="a-leave"),
            pytest.param(is_removed, id="a-removal"),
            pytest.param(lease_ends, id="a-lease-end"),
            pytest.param(call_ends, id="a-call-end"),
            pytest.param(shuts_down, id="a-shutdown"),
        ],
    )
    def test_call_81_the_session_closes_with_the_place_and_one_ok_line_is_written(
        self, scene, fake_video, with_a_figure_after, lines, ends
    ):
        accepted(scene, with_a_figure_after)
        session = fake_video.next_session()
        session.wait_for_update()

        ends(scene)

        session.wait_until_closed()
        assert lines.wait_for_event(ENDED_EVENT, 10)
        scene.call.pass_time(1)
        [line] = ended_lines(lines)
        assert (line["outcome"], len(fake_video.sessions)) == ("ok", 1)

    def test_call_81_a_parked_sponsor_keeps_the_session(self, scene, fake_video, with_a_figure_after, lines):
        accepted(scene, with_a_figure_after)
        session = fake_video.next_session()
        session.wait_for_update()

        scene.owner.drop()
        scene.call.pass_time(1)

        assert (session.is_closed(), ended_lines(lines)) == (False, [])

    @pytest.mark.parametrize("agent_joins_again", [False, True], ids=["the-agent-stays-away", "the-agent-joins-again"])
    def test_call_81_a_place_that_ends_while_its_session_opens_closes_the_session_once_it_is_handed_over(
        self, scene, fake_video, with_a_figure_after, lines, agent_joins_again
    ):
        fake_video.opening.clear()
        opening = threading.Thread(target=scene.call.pass_time, args=(0,))
        accepted_without_opening = accept_with(scene, None, looks=with_a_figure_after)
        assert not accepted_without_opening.is_error
        opening.start()
        assert fake_video.upgrade_asked.wait(10)

        leaves(scene)
        if agent_joins_again:
            assert not accept_with(scene, None).is_error
        fake_video.opening.set()
        opening.join(10)

        session = fake_video.next_session()
        session.wait_until_closed()
        assert lines.wait_for_event(ENDED_EVENT, 10)
        [line] = ended_lines(lines)
        assert line["outcome"] == "ok"


class TestTheAppsShutdown:
    def test_call_81_a_session_still_waiting_for_its_update_closes_and_writes_its_line_when_the_app_shuts_down(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        fake_video.updates = False
        opening = threading.Thread(target=accepted, args=(scene, with_a_figure_after))
        opening.start()
        session = fake_video.next_session()
        session.wait_for_update()

        scene.call.stop()
        opening.join(10)

        session.wait_until_closed()
        [line] = ended_lines(lines)
        assert (line["outcome"], session.close_code) == ("ok", CloseCode.NORMAL_CLOSURE)

    def test_call_81_an_upgrade_still_waiting_for_the_service_ends_and_writes_its_line_when_the_app_shuts_down(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        fake_video.opening.clear()
        opening = threading.Thread(target=accepted, args=(scene, with_a_figure_after))
        opening.start()
        assert fake_video.upgrade_asked.wait(10)

        scene.call.stop()
        opening.join(10)
        fake_video.opening.set()

        [line] = ended_lines(lines)
        assert line["outcome"] == "ok"


class TestAnAcceptWhoseAgentHoldsNoPlace:
    def test_call_81_no_open_starts_for_an_accept_after_which_the_agent_is_no_longer_joined(self):
        scheduler = ManualScheduler()
        participation = SimpleNamespace(video_looks=VideoLooks(Places()))
        opening = video_session.VideoOpening(
            None,
            relying_party=None,
            turn_relay=None,
            participation=lambda: participation,
            scheduler=scheduler,
            wall_clock=time.time,
        )

        opening.after_accept(Person("issuer", "anna"), AcceptedVideoLooks("room", (ESTABLISHED,)), None)

        assert scheduler.pending_timers() == []


class TestTheTokensAreHeldNowhereAfterTheUpgrade:
    def test_the_open_connection_holds_none_of_the_headers_it_upgraded_with(self, fake_video):
        sent = caller_headers(workload_token="scenario-workload-token", person_token="scenario-person-token")

        async def held_after_the_open():
            connection = await video_session.open_connection(fake_video.origin, ESTABLISHED, sent, [])
            try:
                return [value for _, value in connection.request.headers.raw_items()]
            finally:
                await connection.close()

        held = asyncio.run(held_after_the_open())

        assert [value for value in held if value in sent.values()] == []


class TestTheVoiceAndTheAcceptNeverWaitOnVideo:
    @pytest.mark.parametrize("gate", ["health", "exchange", "grant", "turn", "upgrade"])
    def test_call_81_with_a_step_of_the_open_held_back_the_accept_answers_and_the_sentence_plays_at_once(
        self, scene, fake_video, turn_gate, with_a_figure_after, gate
    ):
        release, asked = held_back(gate, scene.call, fake_video, turn_gate)
        assert not accept_with(scene, None, looks=with_a_figure_after).is_error
        opening = threading.Thread(target=scene.call.pass_time, args=(0,))
        opening.start()
        try:
            assert asked.wait(10), f"the open never reached the {gate}"

            spoken = speak(scene, seconds=SENTENCE_SECONDS)
        finally:
            release()
            opening.join(10)

        assert not spoken.is_error
        assert fake_video.next_session().look == ESTABLISHED


def held_back(gate, call, fake_video, turn_gate):
    """Hold one step of the open back; what lets it go on and what is set once the open has asked it. The exchange and
    the grant are held for the video's scope alone, so the sentence's own grants pass."""
    match gate:
        case "health":
            fake_video.health_open.clear()
            return fake_video.health_open.set, fake_video.health_asked
        case "exchange":
            hold = call.issuer.hold(TOKEN_EXCHANGE_GRANT, scope=VIDEO_SCOPE)
            return hold.release.set, hold.parked
        case "grant":
            hold = call.issuer.hold(CLIENT_CREDENTIALS_GRANT, scope=VIDEO_SCOPE)
            return hold.release.set, hold.parked
        case "turn":
            turn_gate.issuing.clear()
            return turn_gate.issuing.set, turn_gate.asked
        case _:
            fake_video.opening.clear()
            return fake_video.opening.set, fake_video.upgrade_asked


class TestNoCredentialLeaksToALog:
    def test_the_captured_lines_hold_no_token_credential_or_frame_with_the_sockets_logger_at_debug(
        self, scene, fake_video, with_a_figure_after, lines
    ):
        accepted(scene, with_a_figure_after)
        session = fake_video.next_session()
        session.wait_for_update()
        leaves(scene)
        session.wait_until_closed()
        assert lines.wait_for_event(ENDED_EVENT, 10)

        [video_token] = tokens_exchanged_for(scene.call, VIDEO_SCOPE)
        [workload] = scene.call.issuer.access_tokens_issued(CLIENT_CREDENTIALS_GRANT)
        logged = json.dumps(list(lines), default=str)
        assert [
            secret
            for secret in (scene.token, video_token, workload, PROVIDER_CREDENTIAL, PROVIDER_USERNAME)
            if secret in logged
        ] == []


class TestEachAudienceHasItsOwnToken:
    def test_a_speech_token_never_reaches_the_video_service_and_a_video_token_never_reaches_the_speech_service(
        self, scene, fake_video, speech_origin, with_a_figure_after
    ):
        accepted(scene, with_a_figure_after)
        session = fake_video.next_session()
        speak(scene)

        [speech_token] = tokens_exchanged_for(scene.call, SPEECH_SCOPE)
        [video_token] = tokens_exchanged_for(scene.call, VIDEO_SCOPE)
        spoken_with = [header_of(request, AUTHORIZATION_HEADER) for request in speech_requests(speech_origin)]
        assert (session.header(AUTHORIZATION_HEADER), spoken_with) == (
            f"Bearer {video_token}",
            [f"Bearer {speech_token}"],
        )

    def test_an_accept_without_a_video_look_exchanges_nothing_for_the_video_audience(
        self, scene, fake_video, figure_served
    ):
        accepted(scene, [figure_look(figure_url())])

        assert (exchanges_for(scene.call, VIDEO_SCOPE), digest_of(FILE) in scene.joined["figure"]) == ([], True)
        assert scene.joined["figure"].startswith(FIGURE_FILES.path)

    def test_the_video_scope_enters_no_persons_sign_in(self, video_service, shared_stores):
        feature = ParticipationFeature(
            "https://hopin.test.invalid", redis=shared_stores.async_redis, realm=TEST_REALM, video=video_service
        )

        assert feature.sign_in_scopes == ()
