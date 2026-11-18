"""Whether an agent listens to its standing, as every HopIn process answers it from the Redis they share: the offers of
each process follow a listen opened through any of them."""

from contextlib import ExitStack
from dataclasses import dataclass
from uuid import uuid4

import pytest
from agents.member import STANDING_URI

from hopin.agent.mcp import LISTENERS_READ_FAILED_EVENT
from hopin.participation.vocabulary import ParticipationSignal
from hopin.stores.redis import connect_shared_stores
from tests.stores.support.redis_gate import RedisGate
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.dialed_call import DialedCall, Seat, serving_dialed_call
from tests.support.fake_issuer import FakeIssuer
from tests.support.log_lines import captured_lines

pytestmark = pytest.mark.shared_stores

OWNER_SUBJECT = "owner-subject"
AGENT_NAME = "Mozart"
LOGGED_WITHIN_SECONDS = 10


@dataclass(frozen=True)
class Process:
    """One HopIn with its own event loop, Redis connections and subscription, the way one pod runs; its owner sits in
    a call of their own, and the agent has called it once, so that its lease runs there."""

    call: DialedCall
    gate: RedisGate
    owner: Seat
    agent_token: str


@pytest.fixture
def agent_subject():
    """An agent no test on another worker shares: Redis delivers a listening turn to every database."""
    return f"agent-subject-{uuid4().hex}"


@pytest.fixture
def processes(sign_in_settings, redis_url, agent_subject):
    """Two processes against one Redis, each through a gate of its own."""
    sign_in = sign_in_settings()
    with ExitStack() as stack:
        started = []
        for _ in range(2):
            gate = RedisGate(redis_url)
            stack.callback(gate.close)
            call = stack.enter_context(
                serving_dialed_call(
                    sign_in,
                    FakeIssuer(sign_in),
                    {OWNER_SUBJECT: ADDERS_GROUP},
                    shared_stores=connect_shared_stores(gate.url),
                )
            )
            _, login = call.member(subject=OWNER_SUBJECT, groups=(ADDERS_GROUP,))
            token = call.agent_token(owner=OWNER_SUBJECT, subject=agent_subject, agent_name=AGENT_NAME)
            call.standing(token)
            started.append(Process(call, gate, call.seat(login), token))
        yield tuple(started)


def offers_once(seat, wanted):
    """The names in the first offers frame the seat is told that `wanted` accepts; frames before it pass over."""
    while not wanted(
        names := [offer["name"] for offer in seat.frame_of_type(ParticipationSignal.AGENT_OFFERS.value)["offers"]]
    ):
        pass
    return names


def listened_and_lapsed(first, second):
    """A listen through `first` that `second` offers and then, once it lapsed, no longer does: `second` has read who
    listens since its start, so its next offers follow a turn alone and not the read its start made."""
    with first.call.open_listen(first.agent_token, [STANDING_URI]):
        offers_once(second.owner, listing_any)
    offers_once(second.owner, listing_none)


def listing_any(names):
    return bool(names)


def listing_none(names):
    return not names


class TestStandingListenersAcrossProcesses:
    def test_a_listen_opened_through_one_process_offers_the_agent_on_the_other(self, processes):
        first, second = processes
        listened_and_lapsed(first, second)

        with first.call.open_listen(first.agent_token, [STANDING_URI]):
            assert offers_once(second.owner, listing_any) == [AGENT_NAME]

    def test_after_the_listen_ends_and_its_allowance_the_agent_is_offered_nowhere(self, processes):
        first, _ = processes
        with first.call.open_listen(first.agent_token, [STANDING_URI]):
            offered = [offers_once(process.owner, listing_any) for process in processes]

        assert (offered, [offers_once(process.owner, listing_none) for process in processes]) == (
            [[AGENT_NAME], [AGENT_NAME]],
            [[], []],
        )

    def test_a_failed_listeners_read_offers_no_agent_and_logs_the_closed_code(self, processes):
        first, second = processes
        another_agent = first.call.agent_token(owner="another-owner-subject", subject=f"agent-subject-{uuid4().hex}")

        with first.call.open_listen(first.agent_token, [STANDING_URI]):
            offers_once(second.owner, listing_any)
            second.gate.refuse_requests()
            with captured_lines() as lines:
                first.call.listen(another_agent, [STANDING_URI])
                logged = lines.wait_for_event(LISTENERS_READ_FAILED_EVENT, LOGGED_WITHIN_SECONDS)
                offers = offers_once(second.owner, lambda names: AGENT_NAME not in names)

        assert (logged, offers) == (True, [])
