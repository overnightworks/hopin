import asyncio
import threading
from dataclasses import dataclass
from functools import partial
from typing import NamedTuple

import pytest
import pytest_asyncio
from agents.member import ACCEPT_INVITATION_TOOL, LEASE_SECONDS

from hopin.participation.feature import Participation
from hopin.participation.vocabulary import ParticipationSignal
from hopin.signaling.handler import SignalingHandler
from tests.participation.support.own_accounts import WALL_CLOCK_SECONDS, GroupReadLog
from tests.signaling.support.browsers import Link
from tests.support.dialed_call import accept_arguments, serving_dialed_call
from tests.support.fake_issuer import FakeIssuer
from tests.support.signaling_fakes import RecordingFeature, SocketAccountResolver, WitnessFeatureBuilder


class Change(NamedTuple):
    person: object
    states: tuple


class Changes(list):
    """What each `on_changed` call named, in order: the person and the states of that person's standings readable at
    the moment of the call. `watch` names the feature whose standings are read."""

    feature = None

    def watch(self, feature):
        self.feature = feature

    def __call__(self, person):
        self.append(Change(person, tuple(standing.state for standing in self.feature.standings_of(person))))


@pytest.fixture
def changes():
    return Changes()


@pytest.fixture
def window_changes():
    """The (person, room) each `on_window_changed` call named, in order."""
    return []


@pytest.fixture
def adders():
    """The owner's subject mapped to the group that may add the owner's agent; a module that adds overrides it."""
    return {}


@pytest.fixture
def groups_of_seat():
    """The groups the issuer holds for the seat of each name; `None` is a read that fails and an `asyncio.Event` a
    read that waits for it and then finds the adders group. A seat never named holds none."""
    return {}


@pytest.fixture
def group_reads(groups_of_seat):
    """How many times a seat's groups were read."""
    return GroupReadLog()


@pytest.fixture
def handler(room_manager, scheduler, changes, window_changes, adders, groups_of_seat, group_reads):
    async def groups_of(connection):
        group_reads.append(connection.account.name)
        groups = groups_of_seat.get(connection.account.name, frozenset())
        if isinstance(groups, asyncio.Event):
            await groups.wait()
            return frozenset(adders.values())
        return groups

    handler = SignalingHandler(
        room_manager,
        SocketAccountResolver(),
        scheduler,
        [
            partial(
                Participation,
                scheduler=scheduler,
                lease_seconds=LEASE_SECONDS,
                on_changed=changes,
                on_window_changed=lambda person, room_id: window_changes.append((person, room_id)),
                wall_clock=lambda: WALL_CLOCK_SECONDS,
                adders=adders,
                groups_of=groups_of,
            )
        ],
    )
    [feature] = handler.features
    changes.watch(feature)
    return handler


@pytest.fixture
def participation(handler) -> Participation:
    [feature] = handler.features
    return feature


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


OWNER_SUBJECT = "subject-owner"
ADDERS = "/apps/hopin/agent-adders"


@dataclass(eq=False)
class SeatChangeWitness(RecordingFeature):
    """A call feature that sets `announced` when the call core announces a seat's change."""

    announced: threading.Event | None = None

    def seat_changed(self, peer_id):
        self.announced.set()


@pytest.fixture
def seat_changes():
    """Set whenever the call core announces a change of a seat; the test clears it before it causes one."""
    return threading.Event()


@pytest.fixture
def adding_call_adders():
    """The owners of the adding call mapped to their adders group; a module that adds another owner extends it."""
    return {OWNER_SUBJECT: ADDERS}


@pytest.fixture
def figure_sources():
    """The figure sources the adding call allows; a module about figures names its own."""
    return ()


@pytest.fixture
def figure_transport():
    """The figures' source behind the transport port; a module about figures brings its own."""
    return


@pytest.fixture
def adding_call(sign_in_settings, seat_changes, adding_call_adders, figure_sources, figure_transport):
    """HopIn whose adders group is mapped to the owner `OWNER_SUBJECT`."""
    sign_in = sign_in_settings()
    with serving_dialed_call(
        sign_in,
        FakeIssuer(sign_in),
        adding_call_adders,
        more_call_features=(WitnessFeatureBuilder(lambda rooms: SeatChangeWitness(rooms, announced=seat_changes)),),
        figure_sources=figure_sources,
        figure_transport=figure_transport,
    ) as call:
        yield call


class Scene:
    """The owner in a call, a member of the adders group in it, and the owner's agent that has called once."""

    agent_name = "Mozart"
    agent_username = "agent-subject-username"
    owner_subject = OWNER_SUBJECT

    def __init__(self, call, *, member_groups=(ADDERS,)):
        self.call = call
        self.owner_account, self.owner_login = call.member(subject=OWNER_SUBJECT, groups=(ADDERS,))
        self.owner = call.seat(self.owner_login)
        self.member_account, self.member_login = call.member(groups=member_groups)
        self.member = call.seat(self.member_login, self.owner.room_id)
        self.token = call.agent_token(
            owner=OWNER_SUBJECT,
            subject="agent-subject",
            agent_name=self.agent_name,
            preferred_username=self.agent_username,
        )
        call.standing(self.token)  # the first call registers the agent

    def key_of_offer(self):
        [offer] = self.member.frame_of_type(ParticipationSignal.AGENT_OFFERS.value)["offers"]
        return offer["agent_key"]

    def key_of_offer_named(self, name):
        offers = self.member.frame_of_type(ParticipationSignal.AGENT_OFFERS.value)["offers"]
        return next(offer["agent_key"] for offer in offers if offer["name"] == name)

    def add(self, key):
        self.member.send({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": key})

    def invite(self):
        """The member adds the agent, and the invitation is open once the offers have been told again."""
        self.add(self.key_of_offer())
        self.member.frame_of_type(ParticipationSignal.AGENT_OFFERS.value)

    def standings(self):
        return self.call.standing(self.token)

    def accept(self, standing):
        return self.call.call(self.token, ACCEPT_INVITATION_TOOL, accept_arguments(standing))


@pytest.fixture
def scene_of(adding_call):
    """The scene, with the options its arrangement takes."""

    def build(**options):
        return Scene(adding_call, **options)

    return build


@pytest.fixture
def scene(scene_of):
    return scene_of()
