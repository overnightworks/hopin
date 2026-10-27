import asyncio
import logging
import secrets
from http import HTTPStatus

import httpx2
import pytest
import structlog.testing
from agent_runtime_client.wire import FailureCode, RejectionCode

from hopin.config.constants import GameRefusal
from hopin.signaling.game import Question, Refused
from tests.agents.support.world import build_world, cancelled, connection_with, wait_in_a_worker
from tests.support.fake_agent_service import (
    PERSONS_ACCESS_ECHO,
    CallsATool,
    CancelsTheRun,
    EndsEarly,
    Fails,
    Holds,
    Redirects,
    Rejects,
    SendsBytes,
    Succeeds,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

LOGGERS_THAT_MUST_STAY_SILENT = ("httpx2", "httpcore2", "urllib3", "webauth")


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


class TestTokens:
    async def test_ask_sends_the_starters_access_token_and_the_workload_token(self, world):
        starter, member = world.starter()

        outcome = await world.game_master.ask(starter, ())

        [run] = world.service.runs
        assert outcome == Question(Succeeds().text)
        assert (run.subject, run.workload_admitted) == (member.account.id, True)
        assert world.grant_types() == ["refresh_token", "client_credentials"]

    @pytest.mark.parametrize("cookie", [None, "__Host-session_id=forged"])
    async def test_ask_without_a_session_cookie_is_sign_in_again_before_any_grant(self, world, cookie):
        outcome = await world.game_master.ask(connection_with(cookie), ())

        assert outcome == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.issuer.exchanges, world.service.runs) == ([], [])

    async def test_ask_from_a_session_without_a_refresh_token_is_sign_in_again(self, world):
        starter, _ = world.starter(with_refresh_token=False)

        assert await world.game_master.ask(starter, ()) == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.issuer.exchanges, world.service.runs) == ([], [])

    async def test_ask_whose_refresh_token_the_issuer_refuses_is_sign_in_again(self, world):
        starter, _ = world.starter()
        world.issuer.refuses_refresh_grants = True

        assert await world.game_master.ask(starter, ()) == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.grant_types(), world.service.runs) == (["refresh_token"], [])

    async def test_ask_from_an_ended_session_is_sign_in_again(self, world):
        starter, member = world.starter()
        world.stores.sessions.delete(member.session.id)

        assert await world.game_master.ask(starter, ()) == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert world.service.runs == []

    async def test_ask_while_the_issuer_is_down_is_unavailable(self, world):
        starter, _ = world.starter()
        world.issuer.token_endpoint_is_down = True

        assert await world.game_master.ask(starter, ()) == Refused(GameRefusal.UNAVAILABLE)
        assert world.service.runs == []

    async def test_ask_whose_workload_grant_is_refused_is_unavailable_and_posts_nothing(self, world):
        starter, _ = world.starter()
        world.issuer.refuses_workload_grants = True

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, ())

        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert world.service.runs == []
        assert [log for log in logs if log["event"] == "game_master_token_refused"] == [
            {
                "event": "game_master_token_refused",
                "log_level": "info",
                "token": "workload",
                "issuer_reason": "token-request-refused",
                "idp_error": "unauthorized_client",
            }
        ]


class TestHeldGrants:
    async def test_a_run_cancelled_during_its_refresh_grant_asks_for_no_workload_token_and_posts_nothing(self, world):
        starter, _ = world.starter()
        hold = world.issuer.hold("refresh_token")
        run = asyncio.create_task(world.game_master.ask(starter, ()))
        await wait_in_a_worker(hold.parked)

        await cancelled(run)
        hold.release.set()
        await wait_in_a_worker(world.sessions.stored_refresh_token)
        await asyncio.sleep(0)

        assert (world.grant_types(), world.service.runs) == (["refresh_token"], [])

    async def test_a_run_cancelled_during_its_workload_grant_posts_nothing(self, world):
        starter, _ = world.starter()
        hold = world.issuer.hold("client_credentials")
        run = asyncio.create_task(world.game_master.ask(starter, ()))
        await wait_in_a_worker(hold.parked)

        await cancelled(run)
        hold.release.set()
        await wait_in_a_worker(hold.answered)
        await asyncio.sleep(0)

        assert world.service.runs == []

    async def test_a_session_ended_during_the_refresh_grant_is_sign_in_again_and_its_token_goes_unused(self, world):
        starter, member = world.starter()
        hold = world.issuer.hold("refresh_token")
        run = asyncio.create_task(world.game_master.ask(starter, ()))
        await wait_in_a_worker(hold.parked)

        world.stores.sessions.delete(member.session.id)
        hold.release.set()

        assert await run == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.grant_types(), world.service.runs) == (["refresh_token"], [])

    async def test_the_next_run_after_a_cancelled_refresh_grant_sends_the_rotated_token_and_succeeds(self, world):
        starter, member = world.starter()
        hold = world.issuer.hold("refresh_token")
        first = asyncio.create_task(world.game_master.ask(starter, ()))
        await wait_in_a_worker(hold.parked)
        await cancelled(first)
        hold.release.set()
        await wait_in_a_worker(world.sessions.stored_refresh_token)
        rotated = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()

        outcome = await world.game_master.ask(starter, ())

        refresh_tokens_sent = [request.form.get("refresh_token") for request, _ in world.issuer.exchanges]
        assert outcome == Question(Succeeds().text)
        assert refresh_tokens_sent[1] == rotated
        assert [run.subject for run in world.service.runs] == [member.account.id]


class TestRejections:
    @pytest.mark.parametrize(
        ("rejection", "expected"),
        [
            pytest.param(
                Rejects(HTTPStatus.FORBIDDEN, RejectionCode.NOT_ENABLED),
                Refused(GameRefusal.NO_ACCESS),
                id="403-not-enabled",
            ),
            pytest.param(
                Rejects(HTTPStatus.FORBIDDEN, RejectionCode.FORBIDDEN),
                Refused(GameRefusal.NO_ACCESS),
                id="403-forbidden",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="17"),
                Refused(GameRefusal.BUSY, retry_after_seconds=17),
                id="429-retry-after-17",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY),
                Refused(GameRefusal.BUSY, retry_after_seconds=30),
                id="429-retry-after-missing",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="soon"),
                Refused(GameRefusal.BUSY, retry_after_seconds=30),
                id="429-retry-after-invalid",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="99999"),
                Refused(GameRefusal.BUSY, retry_after_seconds=600),
                id="429-retry-after-99999",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="0"),
                Refused(GameRefusal.BUSY, retry_after_seconds=1),
                id="429-retry-after-0",
            ),
            pytest.param(
                Rejects(HTTPStatus.TOO_MANY_REQUESTS, RejectionCode.BUSY, retry_after="000017"),
                Refused(GameRefusal.BUSY, retry_after_seconds=17),
                id="429-retry-after-leading-zeros",
            ),
            pytest.param(
                Rejects(HTTPStatus.SERVICE_UNAVAILABLE, RejectionCode.UNAVAILABLE),
                Refused(GameRefusal.UNAVAILABLE),
                id="503",
            ),
            pytest.param(
                Rejects(HTTPStatus.UNAUTHORIZED, RejectionCode.UNAUTHORIZED), Refused(GameRefusal.UNAVAILABLE), id="401"
            ),
            pytest.param(
                Rejects(HTTPStatus.FORBIDDEN, RejectionCode.BUSY), Refused(GameRefusal.UNAVAILABLE), id="403-busy"
            ),
            pytest.param(
                SendsBytes(b"<html>no</html>", status=HTTPStatus.FORBIDDEN, content_type="text/html"),
                Refused(GameRefusal.UNAVAILABLE),
                id="403-without-a-rejection-body",
            ),
        ],
    )
    async def test_a_refused_run_maps_to_its_refusal(self, world, rejection, expected):
        starter, _ = world.starter()
        world.service.answer_next(rejection)

        assert await world.game_master.ask(starter, ()) == expected

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(Redirects("http://elsewhere.test/runs"), id="307"),
            pytest.param(Rejects(HTTPStatus.SERVICE_UNAVAILABLE, RejectionCode.UNAVAILABLE), id="503"),
        ],
    )
    async def test_a_redirect_or_a_503_is_one_request_and_unavailable(self, world, answer):
        starter, _ = world.starter()
        world.service.answer_next(answer)

        outcome = await world.game_master.ask(starter, ())

        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert (len(world.service.runs), world.service.transports_made) == (1, 1)


class TestStream:
    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(SendsBytes(b"{}", content_type="application/json"), id="200-not-an-event-stream"),
            pytest.param(Fails(), id="failed"),
            pytest.param(CancelsTheRun(), id="cancelled"),
            pytest.param(CallsATool(), id="tool-call"),
            pytest.param(EndsEarly(), id="truncated"),
            pytest.param(SendsBytes(b"event: nonsense\n\n"), id="malformed"),
        ],
    )
    async def test_a_run_that_does_not_succeed_is_unavailable_and_closes_its_stream(self, world, answer):
        starter, _ = world.starter()
        world.service.answer_next(answer)

        outcome = await world.game_master.ask(starter, ())

        [run] = world.service.runs
        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert run.closed.is_set()

    async def test_a_transport_error_is_unavailable(self, world):
        starter, _ = world.starter()

        def refuse_to_connect(request):
            raise httpx2.ConnectError("refused", request=request)

        game_master = world.game_master_over(lambda: httpx2.MockTransport(refuse_to_connect))

        assert await game_master.ask(starter, ()) == Refused(GameRefusal.UNAVAILABLE)

    @pytest.mark.parametrize(("answer_length", "expected_length"), [(599, 599), (600, 600), (601, 600), (2000, 600)])
    async def test_an_answer_is_cut_to_600_characters_including_the_ellipsis(
        self, world, answer_length, expected_length
    ):
        starter, _ = world.starter()
        text = "<b>" + "x" * (answer_length - 3)
        world.service.answer_next(Succeeds(text))

        outcome = await world.game_master.ask(starter, ())

        assert len(outcome.text) == expected_length
        expected = text if answer_length <= 600 else text[:599] + "…"
        assert outcome.text == expected


class TestWhatLeaves:
    async def test_the_request_holds_the_quiz_prompt_each_earlier_question_and_the_next_request_and_no_model(
        self, world
    ):
        starter, _ = world.starter()

        await world.game_master.ask(starter, ("Frage eins?", "Frage zwei?"))

        [run] = world.service.runs
        request = run.request
        assert (request.purpose, request.model, request.roles, request.tools) == ("quiz", None, ["admin", "user"], {})
        assert request.turn.system.startswith("Du bist der Spielleiter")
        assert [(message.role.value, message.text) for message in request.turn.messages] == [
            ("user", "Stelle die nächste Quizfrage."),
            ("assistant", "Frage eins?"),
            ("user", "Stelle die nächste Quizfrage."),
            ("assistant", "Frage zwei?"),
            ("user", "Stelle die nächste Quizfrage."),
        ]
        assert {"authorization", "agent-workload-token"} <= run.header_names


@pytest.fixture
def stdlib_records():
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(logging.Formatter().format(record))

    handler = Keep(logging.DEBUG)
    loggers = [logging.getLogger(name) for name in LOGGERS_THAT_MUST_STAY_SILENT]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    yield records
    for logger, level in zip(loggers, levels, strict=True):
        logger.removeHandler(handler)
        logger.setLevel(level)


class TestNothingLeaksIntoLogs:
    @pytest.mark.parametrize(
        "answer_with_the_secrets",
        [
            pytest.param(
                Rejects(HTTPStatus.SERVICE_UNAVAILABLE, RejectionCode.UNAVAILABLE, message="{secrets}"),
                id="rejection-message",
            ),
            pytest.param(Fails(FailureCode.INTERNAL_ERROR, message="{secrets}"), id="failed-message"),
            pytest.param(SendsBytes(b"id: 0\nevent: run_started\ndata: {secrets}\n\n"), id="malformed-frame"),
        ],
    )
    async def test_neither_the_token_nor_the_game_text_reaches_a_log_or_the_outcome(
        self, world, stdlib_records, answer_with_the_secrets
    ):
        sentinel = f"Sentinel-Frage {secrets.token_hex(8)}?"
        starter, member = world.starter()
        embedded = f"{PERSONS_ACCESS_ECHO} {sentinel}"
        world.service.answer_next(_with_secrets(answer_with_the_secrets, embedded))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, (sentinel,))

        [run] = world.service.runs
        refresh_token = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()
        shown = repr(logs) + "".join(stdlib_records) + repr(outcome)
        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        for secret in (run.person_token, refresh_token, sentinel):
            assert secret not in shown
        assert [log["event"] for log in logs if log["event"] == "agent_service_refused"] == ["agent_service_refused"]

    async def test_a_successful_answer_carries_the_question_and_logs_nothing_of_it(self, world, stdlib_records):
        starter, _ = world.starter()
        question = f"Sentinel-Frage {secrets.token_hex(8)}?"
        world.service.answer_next(Succeeds(question))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, ())

        assert outcome == Question(question)
        assert question not in repr(logs) + "".join(stdlib_records)


def _with_secrets(answer, embedded):
    match answer:
        case Rejects():
            return Rejects(answer.status, answer.code, message=embedded, retry_after=answer.retry_after)
        case Fails():
            return Fails(answer.code, message=embedded)
        case SendsBytes():
            return SendsBytes(answer.body.replace(b"{secrets}", embedded.encode()))
    raise AssertionError(answer)


async def test_a_held_run_closes_its_stream_when_cancelled(world):
    starter, _ = world.starter()
    held = Holds()
    world.service.answer_next(held)
    run = asyncio.create_task(world.game_master.ask(starter, ()))
    await held.started.wait_async()

    await cancelled(run)

    [seen] = world.service.runs
    assert seen.closed.is_set()
