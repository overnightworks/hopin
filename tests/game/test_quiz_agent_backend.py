import asyncio
import secrets

import pytest
import structlog.testing
from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import TurnStopKind
from agent_runtime_client.turn import FailureCode, Principal, RejectionCode, RunFile
from webauth.admission import AppRole

from hopin.agent.backend import QUIZ_AGENT_FILE
from hopin.agent.tools import TOOLS
from hopin.game.quiz import Speaker, Turn
from hopin.game.table import Line, Refused
from hopin.game.vocabulary import GameRefusal
from tests.game.support.fake_agent_backend import (
    CallsATool,
    EndsEarly,
    Fails,
    Rejects,
    SaysThenStops,
    Succeeds,
)
from tests.game.support.world import OPENING, build_world, cancelled, connection_with, wait_in_a_worker

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

# The log shows no more of a secret than this many leading characters.
SHOWN_PREFIX_LENGTH = 16
TURN_REFUSED = "game_master_turn_refused"
PARTIAL_BY_ENDING = [
    pytest.param(Rejects(RejectionCode.NOT_ENABLED), Refused(GameRefusal.NO_ACCESS), id="refused-not-enabled"),
    pytest.param(Rejects(RejectionCode.BUSY, retry_after_s=9), Refused(GameRefusal.BUSY, 9), id="refused-busy"),
    pytest.param(Fails(), Refused(GameRefusal.UNAVAILABLE), id="failed"),
]
NO_ACCESS_CODES = [RejectionCode.NOT_ENABLED, RejectionCode.FORBIDDEN]


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


class TestThePerson:
    async def test_ask_reads_the_starters_groups_on_one_refresh_grant_and_asks_for_no_other_token(self, world):
        """GM-20: Ask reads the starters groups on one refresh grant and asks for no other token."""
        starter, member = world.starter()
        world.issuer.grant_groups(member.account.id, ("/scenario/one", "/scenario/two"))

        outcome = await world.game_master.ask(starter, OPENING)

        [run] = world.backend.runs
        assert outcome == Line(Succeeds().text)
        assert run.groups == {"/scenario/one", "/scenario/two"}
        assert world.grant_types() == ["refresh_token"]

    @pytest.mark.parametrize("cookie", [None, "__Host-session_id=forged"])
    async def test_ask_without_a_session_cookie_is_sign_in_again_before_any_grant(self, world, cookie):
        outcome = await world.game_master.ask(connection_with(cookie), OPENING)

        assert outcome == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.issuer.exchanges, world.backend.runs) == ([], [])

    async def test_ask_from_a_session_without_a_refresh_token_is_sign_in_again(self, world):
        starter, _ = world.starter(with_refresh_token=False)

        assert await world.game_master.ask(starter, OPENING) == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.issuer.exchanges, world.backend.runs) == ([], [])

    async def test_ask_whose_refresh_token_the_issuer_refuses_is_sign_in_again(self, world):
        starter, _ = world.starter()
        world.issuer.refuses_refresh_grants = True

        assert await world.game_master.ask(starter, OPENING) == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.grant_types(), world.backend.runs) == (["refresh_token"], [])

    async def test_ask_from_an_ended_session_is_sign_in_again(self, world):
        starter, member = world.starter()
        world.stores.sessions.delete(member.session.id)

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.issuer.exchanges, world.backend.runs) == ([], [])
        assert [log for log in logs if log["event"] == "game_master_groups_refused"] == [
            {"event": "game_master_groups_refused", "log_level": "info", "reason": "session-ended"}
        ]

    async def test_ask_is_for_the_starters_verified_identity_never_for_the_apps_own_run(self, world):
        """GM-08: Ask is for the starters verified identity never for the apps own run."""
        starter, member = world.starter()

        await world.game_master.ask(starter, OPENING)

        [run] = world.backend.runs
        assert run.principal == Principal(iss=world.relying_party.issuer, sub=member.account.id)

    @pytest.mark.parametrize("stored", [AppRole.ADMIN, AppRole.USER])
    async def test_ask_names_the_stored_role_by_the_clients_word(self, world, stored):
        starter, _ = world.starter(role=stored)

        await world.game_master.ask(starter, OPENING)

        [run] = world.backend.runs
        assert run.role == stored.value

    async def test_ask_while_the_issuer_is_down_is_unavailable(self, world):
        starter, _ = world.starter()
        world.issuer.token_endpoint_is_down = True

        assert await world.game_master.ask(starter, OPENING) == Refused(GameRefusal.UNAVAILABLE)
        assert world.backend.runs == []

    async def test_a_refresh_grant_without_an_id_token_is_unavailable_and_its_rotation_is_stored(self, world):
        starter, member = world.starter()
        seeded = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()
        world.issuer.omits_the_id_token_of_a_refresh_grant = True

        assert await world.game_master.ask(starter, OPENING) == Refused(GameRefusal.UNAVAILABLE)
        assert world.backend.runs == []
        assert world.stores.sessions.load(member.session.id).refresh_token.get_secret_value() != seeded

    @pytest.mark.parametrize(
        ("idp_error", "logged"),
        [
            pytest.param("unauthorized_client", "unauthorized_client", id="an-oauth-error"),
            pytest.param(secrets.token_urlsafe(32), "other", id="text-outside-the-oauth-vocabulary"),
        ],
    )
    async def test_a_refresh_grant_refused_otherwise_is_unavailable_and_logged_by_a_closed_value(
        self, world, idp_error, logged
    ):
        starter, _ = world.starter()
        world.issuer.refuses_refresh_grants = True
        world.issuer.refresh_refusal_error = idp_error

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert world.backend.runs == []
        assert [log for log in logs if log["event"] == "game_master_groups_refused"] == [
            {
                "event": "game_master_groups_refused",
                "log_level": "info",
                "reason": "issuer-unavailable",
                "issuer_reason": "token-request-refused",
                "idp_error": logged,
            }
        ]


class TestHeldGrants:
    async def test_a_run_cancelled_during_its_refresh_grant_stores_the_rotation_and_starts_no_turn(self, world):
        starter, member = world.starter()
        seeded = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()
        hold = world.issuer.hold("refresh_token")
        run = asyncio.create_task(world.game_master.ask(starter, OPENING))
        await wait_in_a_worker(hold.parked)

        await cancelled(run)
        hold.release.set()
        await wait_in_a_worker(world.sessions.stored_refresh_token)
        await asyncio.sleep(0)

        assert (world.grant_types(), world.backend.runs) == (["refresh_token"], [])
        assert world.stores.sessions.load(member.session.id).refresh_token.get_secret_value() != seeded

    async def test_a_session_ended_during_the_refresh_grant_is_sign_in_again_and_not_recreated(self, world):
        starter, member = world.starter()
        hold = world.issuer.hold("refresh_token")
        run = asyncio.create_task(world.game_master.ask(starter, OPENING))
        await wait_in_a_worker(hold.parked)

        world.stores.sessions.delete(member.session.id)
        hold.release.set()

        assert await run == Refused(GameRefusal.SIGN_IN_AGAIN)
        assert (world.grant_types(), world.backend.runs) == (["refresh_token"], [])
        assert world.stores.sessions.load(member.session.id) is None

    async def test_the_next_run_after_a_cancelled_refresh_grant_sends_the_rotated_token_and_succeeds(self, world):
        starter, member = world.starter()
        hold = world.issuer.hold("refresh_token")
        first = asyncio.create_task(world.game_master.ask(starter, OPENING))
        await wait_in_a_worker(hold.parked)
        await cancelled(first)
        hold.release.set()
        await wait_in_a_worker(world.sessions.stored_refresh_token)
        rotated = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()

        outcome = await world.game_master.ask(starter, OPENING)

        refresh_tokens_sent = [request.form.get("refresh_token") for request, _ in world.issuer.exchanges]
        assert outcome == Line(Succeeds().text)
        assert refresh_tokens_sent[1] == rotated
        assert [run.principal.sub for run in world.backend.runs] == [member.account.id]


class TestEndings:
    @pytest.mark.parametrize("said", [(), ("Ein Teil.", "Noch ein Teil.")])
    async def test_a_final_text_is_the_line_alone_whatever_the_agent_said_before(self, world, said):
        """GM-22: A final text is the line alone whatever the agent said before."""
        starter, _ = world.starter()
        world.backend.answer_next(Succeeds("Die ganze Frage?", said=said))

        outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Line("Die ganze Frage?")
        [run] = world.backend.runs
        assert run.closed.is_set()

    @pytest.mark.parametrize(("answer_length", "expected_length"), [(599, 599), (600, 600), (601, 600), (2000, 600)])
    async def test_an_answer_is_cut_to_600_characters_including_the_ellipsis(
        self, world, answer_length, expected_length
    ):
        starter, _ = world.starter()
        text = "<b>" + "x" * (answer_length - 3)
        world.backend.answer_next(Succeeds(text))

        outcome = await world.game_master.ask(starter, OPENING)

        assert len(outcome.text) == expected_length
        assert outcome.text == (text if answer_length <= 600 else text[:599] + "…")

    @pytest.mark.parametrize("code", NO_ACCESS_CODES)
    async def test_a_turn_refused_for_lack_of_access_is_no_access(self, world, code):
        starter, _ = world.starter()
        world.backend.answer_next(Rejects(code))

        assert await world.game_master.ask(starter, OPENING) == Refused(GameRefusal.NO_ACCESS)

    @pytest.mark.parametrize(("retry_after_s", "expected_wait"), [(17, 17), (None, 30), (0, 1), (600, 600)], ids=str)
    async def test_a_busy_turn_waits_as_told_with_a_fallback_and_a_floor(self, world, retry_after_s, expected_wait):
        starter, _ = world.starter()
        world.backend.answer_next(Rejects(RejectionCode.BUSY, retry_after_s=retry_after_s))

        outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Refused(GameRefusal.BUSY, retry_after_seconds=expected_wait)

    @pytest.mark.parametrize("code", list(FailureCode))
    async def test_a_failed_turn_is_unavailable_whatever_its_code_and_logs_that_code(self, world, code):
        starter, _ = world.starter()
        world.backend.answer_next(Fails(code))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert [log["code"] for log in logs if log["event"] == TURN_REFUSED] == [code.value]

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(CallsATool(), id="tool-call"),
            pytest.param(EndsEarly(), id="ends-without-a-final-event"),
            pytest.param(Succeeds("", files=(RunFile(name="note.txt", content=b"unread"),)), id="final-without-words"),
        ],
    )
    async def test_a_turn_without_a_final_text_is_unavailable_and_closes_its_stream(self, world, answer):
        starter, _ = world.starter()
        world.backend.answer_next(answer)

        outcome = await world.game_master.ask(starter, OPENING)

        [run] = world.backend.runs
        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        assert run.closed.is_set()

    @pytest.mark.parametrize(("stop", "expected"), PARTIAL_BY_ENDING)
    async def test_text_spoken_before_a_stop_decides_nothing_and_reaches_no_outcome(self, world, stop, expected):
        starter, _ = world.starter()
        partial = f"Halbe Frage {secrets.token_hex(8)}"
        world.backend.answer_next(SaysThenStops(partial, stop))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == expected
        assert partial not in repr(outcome) + repr(logs)


class TestWhatLeaves:
    async def test_the_request_holds_the_agent_files_model_and_bound_its_prompt_and_the_quiz_so_far_as_turns(
        self, world
    ):
        starter, _ = world.starter()
        transcript = (
            Turn(Speaker.HOPIN, "Erste Bitte."),
            Turn(Speaker.GAME_MASTER, "Frage eins?"),
            Turn(Speaker.HOPIN, "Zweite Bitte."),
        )

        await world.game_master.ask(starter, transcript)

        [run] = world.backend.runs
        request = run.request
        agent = load_agent(QUIZ_AGENT_FILE, list(TOOLS))
        assert (request.agent, request.roles, request.tools) == ("quiz", ["admin", "user"], ())
        assert (request.model, request.max_duration_seconds) == (agent.model, agent.max_duration_seconds)
        assert request.turn.system == agent.prompt
        assert [(message.role.value, message.text) for message in request.turn.messages] == [
            ("user", "Erste Bitte."),
            ("assistant", "Frage eins?"),
            ("user", "Zweite Bitte."),
        ]


class TestNothingLeaksIntoLogs:
    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(
                Succeeds("", files=(RunFile(name="note.txt", content=b"{secrets}"),)), id="final-without-words"
            ),
            pytest.param(SaysThenStops("{secrets}", Fails()), id="failed-after-speaking"),
        ],
    )
    async def test_neither_a_token_nor_a_group_path_nor_the_game_text_reaches_a_log_or_the_outcome(
        self, world, stdlib_records, answer
    ):
        """GM-20: Neither a token nor a group path nor the game text reaches a log or the outcome."""
        sentinel = f"Sentinel-Frage {secrets.token_hex(8)}?"
        group = f"/scenario/{secrets.token_hex(8)}"
        starter, member = world.starter()
        world.issuer.grant_groups(member.account.id, (group,))
        world.backend.answer_next(_with_sentinel(answer, sentinel))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, (*OPENING, Turn(Speaker.GAME_MASTER, sentinel)))

        refresh_token = world.stores.sessions.load(member.session.id).refresh_token.get_secret_value()
        shown = repr(logs) + "".join(stdlib_records) + repr(outcome)
        assert outcome == Refused(GameRefusal.UNAVAILABLE)
        for secret in (refresh_token, group, sentinel):
            assert secret[:SHOWN_PREFIX_LENGTH] not in shown
        assert [log["event"] for log in logs if log["event"] == TURN_REFUSED] == [TURN_REFUSED]

    async def test_a_refusal_is_logged_by_its_closed_values_only(self, world):
        starter, _ = world.starter()
        world.backend.answer_next(Rejects(RejectionCode.FORBIDDEN, detail="transport_lost"))

        with structlog.testing.capture_logs() as logs:
            await world.game_master.ask(starter, OPENING)

        assert [log for log in logs if log["event"] == TURN_REFUSED] == [
            {
                "event": TURN_REFUSED,
                "log_level": "info",
                "kind": TurnStopKind.REFUSED.value,
                "code": RejectionCode.FORBIDDEN.value,
                "detail": "transport_lost",
            }
        ]

    async def test_a_successful_answer_carries_the_question_and_logs_nothing_of_it(self, world, stdlib_records):
        starter, _ = world.starter()
        question = f"Sentinel-Frage {secrets.token_hex(8)}?"
        world.backend.answer_next(Succeeds(question, said=(question,)))

        with structlog.testing.capture_logs() as logs:
            outcome = await world.game_master.ask(starter, OPENING)

        assert outcome == Line(question)
        assert question not in repr(logs) + "".join(stdlib_records)


def _with_sentinel(answer, sentinel):
    match answer:
        case Succeeds():
            return Succeeds(
                text=answer.text,
                files=tuple(RunFile(name=file.name, content=sentinel.encode()) for file in answer.files),
            )
        case SaysThenStops():
            return SaysThenStops(sentinel, answer.stop)
    return answer
