"""Whom a joined agent may hear, at participation's real feature and handler and the agents library's real record."""

import pytest
import pytest_asyncio
from agents.mcp_server.hearing import Sense
from agents.member import LEASE_SECONDS, PlaceRef, place_window_uri
from agents.tools import Principal
from pydantic import ValidationError

from hopin.agent.hearing import MemberHearingRecord
from hopin.agent.mcp import _MemberResources
from hopin.agent.tools import CALL_KIND
from hopin.config.constants import ErrorReason, SignalType
from hopin.participation.chat_window import LINES_PER_WINDOW, LineKind, WindowLine
from hopin.participation.hearing import Hearing, HearingHandle
from hopin.participation.vocabulary import AgentRefusal, HearingRefusal, ParticipationSignal
from tests.participation.support.ends import (
    end_by_the_host,
    leave_by_the_agent,
    leave_by_the_sponsor,
    remove_by_the_host,
    shut_down,
)
from tests.participation.support.own_accounts import (
    ADDERS,
    AGENT,
    OWNER,
    agent_calls_and_listens,
    let_reads_finish,
    offers_sent_to,
)
from tests.participation.support.people import person_named, room_of
from tests.signaling.support.browsers import host_and_guest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

GUEST = person_named("guest")
HOST = person_named("host")


@pytest.fixture
def hears():
    return True


@pytest.fixture
def adders():
    return {OWNER: ADDERS}


@pytest.fixture(autouse=True)
def members(groups_of_seat):
    groups_of_seat[OWNER] = groups_of_seat["guest"] = frozenset({ADDERS})


def switch(agent_id, *, on):
    return {"type": ParticipationSignal.AGENT_HEARING.value, "agent_id": agent_id, "on": on}


def hearing_frames(browser):
    return [(frame["agent_id"], frame["on"]) for frame in browser.frames(ParticipationSignal.AGENT_HEARING)]


def refusals(browser):
    return [frame["reason"] for frame in browser.frames(ParticipationSignal.AGENT_REFUSED)]


def lines_of(participation, person, room_id):
    return participation.read_window(person, room_id).lines


@pytest.fixture
def recorded(member_hearing):
    return RecordedEnds(MemberHearingRecord(member_hearing))


@pytest.fixture
def hearing(recorded, hears):
    return Hearing(recorded, enabled=hears)


def hearers(member_hearing, room_id, seat):
    """The agents the record says hear the seat."""
    return {
        principal.sub for principal in member_hearing.sensing(PlaceRef(kind=CALL_KIND, id=room_id), seat, Sense.HEARING)
    }


def open_hearing(participation, account, seat, *, holding=lambda agent: True):
    """The sound session of the seat, opened as the route opens it: `holding` says which agents hold a token."""
    return participation.open_hearing(account, seat, holding)


async def dial(participation, browser):
    return await participation.dial_in(person_named(browser.name), room_of(browser), "Claude")


@pytest_asyncio.fixture
async def dialed(link, participation):
    """A call of a host and a guest, with the guest's own agent dialed in."""
    host, guest = await host_and_guest(link)
    agent = await dial(participation, guest)
    return host, guest, agent


@pytest_asyncio.fixture
async def invitation_of(link, participation, listeners):
    """An agent with an account of its own, offered to the call of its owner (the host seat) and a member, who adds
    it; the result accepts the invitation."""
    owner = await link.join(OWNER)
    member = await link.join("guest", owner.room_id)
    await agent_calls_and_listens(participation, listeners)
    await let_reads_finish()

    async def add_by(adder_name):
        adder = {OWNER: owner, "guest": member}[adder_name]
        [offer] = offers_sent_to(adder)
        await adder.say({"type": ParticipationSignal.AGENT_ADD.value, "agent_key": offer["agent_key"]})
        await let_reads_finish()
        [standing] = participation.standings_of(AGENT)
        invitation = standing.invitation
        agent = await participation.accept(AGENT, owner.room_id, invitation.invitation_id, invitation.epoch, None)
        return owner, member, agent

    return add_by


class TestTheDefault:
    async def test_call_57_an_agent_its_sponsor_dialed_in_starts_on_and_only_the_sponsor_is_told(
        self, link, participation
    ):
        """CALL-57: A joined agent's sponsor has the agent's hearing switch, which starts on when the sponsor brought
        the agent in by dialing it in."""
        host, guest = await host_and_guest(link)

        agent = await dial(participation, guest)

        assert (hearing_frames(guest), hearing_frames(host)) == ([(agent.agent_id, True)], [])
        assert isinstance(open_hearing(participation, GUEST, guest.peer_id), HearingHandle)

    async def test_call_57_an_agent_the_sponsor_added_from_their_own_seat_starts_on(self, invitation_of):
        """CALL-57: The switch starts on when the sponsor added the agent from their own seat."""
        owner, _member, agent = await invitation_of(OWNER)

        assert hearing_frames(owner) == [(agent.agent_id, True)]

    async def test_call_57_an_agent_another_member_added_starts_off(self, invitation_of, participation):
        """CALL-57: The switch starts off when someone other than the sponsor added the agent."""
        owner, member, agent = await invitation_of("guest")

        assert (hearing_frames(owner), hearing_frames(member)) == ([(agent.agent_id, False)], [])
        assert open_hearing(participation, person_named(OWNER), owner.peer_id) is HearingRefusal.NOBODY_HEARS

    @pytest.mark.parametrize(
        ("adder", "decision"), [(OWNER, True), ("guest", False)], ids=["the_sponsor", "another_member"]
    )
    async def test_call_57_every_join_records_its_default_decision_with_the_record(
        self, invitation_of, recorded, adder, decision
    ):
        """CALL-57: The default of a switch reaches the record as a decision, off as well as on."""
        await invitation_of(adder)

        assert recorded.decisions == [decision]


class TestTheSwitch:
    @pytest.mark.parametrize("on", [False, True], ids=["off", "back_on"])
    async def test_call_57_the_sponsor_switches_the_agent_and_is_answered_with_the_record(
        self, participation, dialed, on
    ):
        """CALL-57: A switch frame from the sponsor sets the switch and is answered with the server's record."""
        _host, guest, agent = dialed
        await guest.say(switch(agent.agent_id, on=False))

        await guest.say(switch(agent.agent_id, on=on))

        assert hearing_frames(guest)[1:] == [(agent.agent_id, False), (agent.agent_id, on)]
        assert isinstance(open_hearing(participation, GUEST, guest.peer_id), HearingHandle) is on

    async def test_call_57_a_seat_that_does_not_sponsor_the_agent_is_refused_and_the_switch_stays(
        self, participation, dialed
    ):
        """CALL-57: Only the agent's sponsor has its switch; any other seat, the host included, is refused."""
        host, guest, agent = dialed

        await host.say(switch(agent.agent_id, on=False))

        assert refusals(host) == [AgentRefusal.NOT_SPONSOR.value]
        assert isinstance(open_hearing(participation, GUEST, guest.peer_id), HearingHandle)

    async def test_call_57_a_switch_of_an_agent_that_is_not_joined_there_is_refused_as_gone(self, dialed):
        """CALL-57: A switch frame for an agent that holds no joined place in the seat's room is refused as gone."""
        host, _guest, _agent = dialed

        await host.say(switch("another-agent", on=True))

        assert refusals(host) == [AgentRefusal.GONE.value]

    @pytest.mark.parametrize("data", [{}, {"agent_id": 7, "on": True}, {"agent_id": "x", "on": "yes"}])
    async def test_call_57_a_switch_without_an_agent_id_and_a_boolean_is_an_invalid_message(self, dialed, data):
        """CALL-57: A switch frame names the agent by its id and says on or off as a boolean."""
        host, _guest, _agent = dialed

        await host.say({"type": ParticipationSignal.AGENT_HEARING.value, **data})

        assert [frame["reason"] for frame in host.frames(SignalType.ERROR)] == [ErrorReason.INVALID_MESSAGE.value]

    async def test_call_57_pausing_the_agent_does_not_end_hearing_but_ends_the_sound_of_the_parked_seat(
        self, participation, dialed
    ):
        """CALL-57: A parked seat is not an end: its sound ends, its switch stays, and it hears on after a resume."""
        _host, guest, _agent = dialed
        handle = open_hearing(participation, GUEST, guest.peer_id)

        await guest.drop()

        assert handle.ended.is_set()
        assert open_hearing(participation, GUEST, guest.peer_id) is HearingRefusal.SEAT_AWAY
        await guest.resume()
        await guest.accept_resumption()
        assert isinstance(open_hearing(participation, GUEST, guest.peer_id), HearingHandle)


@pytest_asyncio.fixture
async def both(participation, invitation_of):
    """An owner's seat with two joined agents it sponsors, both hearing it: the added agent and the owner's own."""
    owner, _member, added = await invitation_of(OWNER)
    own = await dial(participation, owner)
    return owner, added, own


@pytest_asyncio.fixture
async def served(participation, both):
    """The sound session of the owner's seat, served by the added agent, which holds the token."""
    owner, added, _own = both
    return open_hearing(participation, person_named(OWNER), owner.peer_id, holding=lambda agent: agent == added.person)


async def _switched_off(_participation, owner, agent):
    await owner.say(switch(agent.agent_id, on=False))


async def _left(participation, owner, agent):
    await participation.leave(agent.person, owner.room_id)


AN_AGENT_ENDS_HEARING = pytest.mark.parametrize("stops_hearing", [_switched_off, _left], ids=["switch_off", "leave"])


class TestTwoAgentsOfOneSponsor:
    async def test_call_57_a_sentence_reaches_each_agent_whose_switch_is_on(self, participation, both):
        """CALL-57: With both switches on, a heard sentence joins the window of each agent."""
        owner, added, own = both

        participation.heard(owner.peer_id, "to both", began_at=0.0)

        assert [
            [line.text for line in lines_of(participation, agent.person, owner.room_id)] for agent in (added, own)
        ] == [["to both"], ["to both"]]

    async def test_call_57_a_sentence_reaches_only_the_agent_whose_switch_stays_on(self, participation, both):
        """CALL-57: Turning one switch off stops that agent hearing and leaves the other."""
        owner, added, own = both
        await owner.say(switch(own.agent_id, on=False))

        participation.heard(owner.peer_id, "to one", began_at=0.0)

        assert [
            [line.text for line in lines_of(participation, agent.person, owner.room_id)] for agent in (added, own)
        ] == [["to one"], []]

    async def test_call_57_the_handle_is_served_by_an_agent_holding_a_token(self, participation, both):
        """CALL-58: The sound session names one hearing agent, the one that holds a token."""
        owner, added, own = both

        handles = [
            open_hearing(
                participation, person_named(OWNER), owner.peer_id, holding=lambda agent, held=held: agent == held
            )
            for held in (added.person, own.person)
        ]

        assert [handle.agent for handle in handles] == [added.person, own.person]

    @AN_AGENT_ENDS_HEARING
    async def test_call_57_the_handle_goes_on_when_the_agent_not_serving_it_stops_hearing(
        self, participation, both, served, stops_hearing
    ):
        """CALL-57: The handle ends only with the agent that serves it; the other's switch or leave leaves it open."""
        owner, _added, own = both

        await stops_hearing(participation, owner, own)

        assert served.ended.is_set() is False

    @AN_AGENT_ENDS_HEARING
    async def test_call_57_the_handle_ends_when_the_agent_serving_it_stops_hearing_and_the_other_still_hears(
        self, participation, both, served, stops_hearing
    ):
        """CALL-57: Turning the serving agent off or its leave ends the handle; a new one is served by the other."""
        owner, added, own = both

        await stops_hearing(participation, owner, added)

        assert served.ended.is_set()
        assert open_hearing(participation, person_named(OWNER), owner.peer_id).agent == own.person

    async def test_call_57_the_handle_ends_with_the_last_switch_that_goes_off(self, participation, both):
        owner, added, own = both
        handle = open_hearing(participation, person_named(OWNER), owner.peer_id)

        await owner.say(switch(added.agent_id, on=False))
        await owner.say(switch(own.agent_id, on=False))

        assert handle.ended.is_set()
        assert open_hearing(participation, person_named(OWNER), owner.peer_id) is HearingRefusal.NOBODY_HEARS


class TestTheGate:
    async def test_call_57_a_handle_names_the_account_and_has_an_end_event(self, participation, dialed):
        """CALL-57: A seat that its account holds, in its room, which a joined agent hears, gets a handle."""
        _host, guest, _agent = dialed

        handle = open_hearing(participation, GUEST, guest.peer_id)

        assert (handle.account, handle.seat, handle.ended.is_set()) == (GUEST, guest.peer_id, False)

    async def test_call_57_a_handle_names_the_joined_agent_that_hears_the_seat(self, participation, invitation_of):
        """CALL-57: The handle names the person of the joined agent, which for an agent with an account of its own
        is not the account that holds the seat."""
        owner, _member, _agent = await invitation_of(OWNER)

        handle = open_hearing(participation, person_named(OWNER), owner.peer_id)

        assert handle.agent == AGENT

    @pytest.mark.parametrize(
        ("account", "seat_of"),
        [
            pytest.param(HOST, lambda guest: guest.peer_id, id="another_account"),
            pytest.param(GUEST, lambda guest: "unknown-seat", id="a_seat_that_does_not_exist"),
        ],
    )
    async def test_call_57_a_seat_its_account_does_not_hold_in_a_room_is_refused(
        self, participation, dialed, account, seat_of
    ):
        """CALL-57: A seat that the account does not hold, or that sits in no room, gets no handle."""
        _host, guest, _agent = dialed

        assert open_hearing(participation, account, seat_of(guest)) is HearingRefusal.NO_SEAT

    async def test_call_57_a_seat_no_joined_agent_hears_is_refused(self, link, participation):
        """CALL-57: A seat with no joined agent that hears it gets no handle."""
        _host, guest = await host_and_guest(link)

        assert open_hearing(participation, GUEST, guest.peer_id) is HearingRefusal.NOBODY_HEARS

    async def test_call_57_a_seat_with_its_switch_off_is_refused_and_its_handle_ends(self, participation, dialed):
        """CALL-57: Switching the last agent that hears the seat off ends the seat's handle."""
        _host, guest, agent = dialed
        handle = open_hearing(participation, GUEST, guest.peer_id)

        await guest.say(switch(agent.agent_id, on=False))

        assert handle.ended.is_set()
        assert open_hearing(participation, GUEST, guest.peer_id) is HearingRefusal.NOBODY_HEARS

    async def test_call_57_a_seat_has_one_handle_and_a_newer_one_ends_the_older_first(self, participation, dialed):
        """CALL-57: A seat has at most one handle; opening another ends the older one."""
        _host, guest, _agent = dialed
        older = open_hearing(participation, GUEST, guest.peer_id)

        newer = open_hearing(participation, GUEST, guest.peer_id)

        assert (older.ended.is_set(), newer.ended.is_set()) == (True, False)


class TestWhatIsHeard:
    async def test_call_57_a_heard_sentence_is_a_spoken_person_line_under_the_seats_shown_name(
        self, participation, window_changes, dialed
    ):
        """CALL-57: A heard sentence joins the window of the hearing agent as a spoken line of kind person under the
        name the call shows for the seat, and the window change is signalled."""
        host, guest, _agent = dialed

        participation.heard(guest.peer_id, "hello there", began_at=0.0)

        assert lines_of(participation, GUEST, room_of(host)) == (
            WindowLine(1, LineKind.PERSON, "guest", "hello there", own=False, spoken=True),
        )
        assert window_changes == [(GUEST, room_of(host))]

    async def test_call_57_the_agent_reads_a_heard_sentence_as_a_spoken_entry_of_its_window(
        self, participation, dialed
    ):
        """CALL-57: The agent's read of the call's window marks a heard line spoken and not its own."""
        host, guest, _agent = dialed
        participation.chat_said(host.peer_id, room_of(host), "written")
        participation.heard(guest.peer_id, "said aloud", began_at=0.0)
        resources = _MemberResources(lambda: participation)

        window = await resources.read(
            Principal(iss=GUEST.issuer, sub=GUEST.subject),
            place_window_uri(PlaceRef(kind=CALL_KIND, id=room_of(host))),
        )

        assert [(entry.text, entry.kind, entry.own, entry.spoken) for entry in window.entries] == [
            ("written", "person", False, False),
            ("said aloud", "person", False, True),
        ]

    async def test_call_57_only_the_agent_that_hears_the_seat_gets_the_line(self, link, participation):
        """CALL-57: A line goes to the window of each agent that hears the seat and to no other."""
        host, guest = await host_and_guest(link)
        await dial(participation, guest)
        await dial(participation, host)

        participation.heard(guest.peer_id, "from the guest", began_at=0.0)

        assert [line.text for line in lines_of(participation, GUEST, room_of(host))] == ["from the guest"]
        assert lines_of(participation, HOST, room_of(host)) == ()

    @pytest.mark.parametrize("text", ["", "   ", "\n\t", "\ud800"], ids=["empty", "spaces", "whitespace", "surrogate"])
    async def test_call_57_a_sentence_with_no_text_appends_nothing(self, participation, window_changes, dialed, text):
        """CALL-57: An empty sentence, or one that does not encode, appends nothing and signals nothing."""
        host, guest, _agent = dialed

        participation.heard(guest.peer_id, text, began_at=0.0)

        assert (lines_of(participation, GUEST, room_of(host)), window_changes) == ((), [])

    @pytest.mark.parametrize("began_at", [float("nan"), float("inf"), float("-inf")])
    async def test_call_57_a_sentence_that_began_at_no_moment_fails_loudly(self, participation, dialed, began_at):
        """CALL-57: Only a refused text is dropped quietly; a start that is no moment is HopIn's own defect."""
        _host, guest, _agent = dialed

        with pytest.raises(ValidationError):
            participation.heard(guest.peer_id, "a sentence", began_at=began_at)

    async def test_call_57_a_switch_on_in_the_middle_of_a_sentence_loses_it(
        self, participation, scheduler, invitation_of
    ):
        """CALL-57: A sentence that began before the switch was turned on is not heard; one that began at it is."""
        owner, _member, agent = await invitation_of("guest")
        scheduler.skip(10)
        await owner.say(switch(agent.agent_id, on=True))

        participation.heard(owner.peer_id, "began before", began_at=9.0)
        participation.heard(owner.peer_id, "began at the switch", began_at=10.0)

        assert [line.text for line in lines_of(participation, AGENT, owner.room_id)] == ["began at the switch"]

    async def test_call_57_a_sentence_of_a_seat_that_left_is_not_heard(self, participation, window_changes, dialed):
        """CALL-57: A sentence that finishes after its seat left appends nothing."""
        _host, guest, _agent = dialed
        await guest.drop(1000)

        participation.heard(guest.peer_id, "too late", began_at=0.0)

        assert window_changes == []

    async def test_call_57_nothing_is_appended_after_the_switch_goes_off(self, participation, dialed):
        """CALL-57: A sentence that finishes after the switch went off is not heard, whenever it began."""
        host, guest, agent = dialed
        await guest.say(switch(agent.agent_id, on=False))

        participation.heard(guest.peer_id, "too late", began_at=0.0)

        assert lines_of(participation, GUEST, room_of(host)) == ()

    async def test_call_57_the_windows_bounds_hold_with_spoken_lines(self, participation, dialed):
        """CALL-57: Spoken lines are held within the window's bound of the newest lines."""
        host, guest, _agent = dialed

        for number in range(LINES_PER_WINDOW + 1):
            participation.heard(guest.peer_id, f"line {number}", began_at=0.0)

        window = participation.read_window(GUEST, room_of(host))
        assert (len(window.lines), window.starts_at) == (LINES_PER_WINDOW, 2)


class RecordedEnds:
    """The record behind the port, with the decisions and ends participation reports to it, in order."""

    def __init__(self, record):
        self.reported = []
        self.decisions = []
        self._record = record

    def __getattr__(self, name):
        return getattr(self._record, name)

    def allow(self, room_id, seat, agent, sense, *, allowed):
        self.decisions.append(allowed)
        self._record.allow(room_id, seat, agent, sense, allowed=allowed)

    def member_ended(self, room_id, agent):
        self.reported.append("member_ended")
        self._record.member_ended(room_id, agent)

    def speaker_ended(self, room_id, seat):
        self.reported.append("speaker_ended")
        self._record.speaker_ended(room_id, seat)

    def place_ended(self, room_id):
        self.reported.append("place_ended")
        self._record.place_ended(room_id)


class TestTheEnds:
    @pytest.mark.parametrize(
        ("end", "reported"),
        [
            pytest.param(leave_by_the_agent, ["member_ended"], id="leave"),
            pytest.param(remove_by_the_host, ["member_ended"], id="removal"),
            pytest.param(leave_by_the_sponsor, ["member_ended", "speaker_ended"], id="the_sponsors_seat_leaving"),
            pytest.param(end_by_the_host, ["place_ended"], id="the_rooms_end"),
            pytest.param(shut_down, ["place_ended"], id="shutdown"),
        ],
    )
    async def test_call_57_every_exit_path_reports_its_end_to_the_record(
        self, participation, recorded, dialed, end, reported
    ):
        """CALL-57: The agent's leave or removal reports `member_ended`, the seat's leaving for good `speaker_ended`,
        the call's end and shutdown `place_ended`."""
        host, guest, agent = dialed

        await end(participation, host, guest, agent)

        assert recorded.reported == reported

    @pytest.mark.parametrize(
        "end",
        [
            pytest.param(leave_by_the_agent, id="leave"),
            pytest.param(remove_by_the_host, id="removal"),
            pytest.param(leave_by_the_sponsor, id="the_sponsors_seat_leaving"),
            pytest.param(end_by_the_host, id="the_rooms_end"),
            pytest.param(shut_down, id="shutdown"),
        ],
    )
    async def test_call_57_every_exit_path_ends_hearing_at_once_and_the_handle_with_it(
        self, participation, member_hearing, dialed, end
    ):
        """CALL-57: Leave, removal, the sponsor's leaving, the call's end and shutdown each end the record's entries
        and the seat's handle at once."""
        host, guest, agent = dialed
        handle = open_hearing(participation, GUEST, guest.peer_id)

        await end(participation, host, guest, agent)

        assert hearers(member_hearing, room_of(host), guest.peer_id) == set()
        assert handle.ended.is_set()

    @pytest.mark.parametrize(
        "agent_end",
        [pytest.param(leave_by_the_agent, id="leave"), pytest.param(remove_by_the_host, id="removal")],
    )
    async def test_call_57_a_seat_leaving_for_good_after_its_agent_ended_still_reports_its_end(
        self, participation, recorded, dialed, agent_end
    ):
        """CALL-57: The seat's permanent leaving reports `speaker_ended` though no joined agent is left to sponsor."""
        host, guest, agent = dialed
        await agent_end(participation, host, guest, agent)

        await leave_by_the_sponsor(participation, host, guest, agent)

        assert recorded.reported == ["member_ended", "speaker_ended"]

    async def test_call_57_the_end_of_an_agents_lease_ends_hearing_at_once(
        self, participation, scheduler, member_hearing, invitation_of
    ):
        """CALL-57: An agent whose lease runs out stops hearing at once."""
        owner, _member, _agent = await invitation_of(OWNER)
        handle = open_hearing(participation, person_named(OWNER), owner.peer_id)

        await scheduler.advance(LEASE_SECONDS)

        assert hearers(member_hearing, owner.room_id, owner.peer_id) == set()
        assert handle.ended.is_set()

    async def test_call_57_a_rejoined_agent_hears_only_what_began_after_its_new_join(
        self, participation, scheduler, dialed
    ):
        """CALL-57: After a leave and a rejoin, a sentence that began before the rejoin is not heard."""
        host, guest, _agent = dialed
        await participation.leave(GUEST, room_of(host))
        scheduler.skip(10)
        await dial(participation, guest)

        participation.heard(guest.peer_id, "before the rejoin", began_at=5.0)
        participation.heard(guest.peer_id, "after the rejoin", began_at=10.0)

        assert [line.text for line in lines_of(participation, GUEST, room_of(host))] == ["after the rejoin"]

    async def test_call_57_a_switch_after_the_agent_left_is_refused_and_records_nothing(
        self, participation, member_hearing, dialed
    ):
        """CALL-57: A switch frame for an agent that has left is refused and records nothing."""
        host, guest, agent = dialed
        await participation.leave(GUEST, room_of(host))

        await guest.say(switch(agent.agent_id, on=True))

        assert refusals(guest) == [AgentRefusal.GONE.value]
        assert hearers(member_hearing, room_of(host), guest.peer_id) == set()


class TestTheSettingOff:
    @pytest.fixture
    def hears(self):
        return False

    async def test_call_57_the_switch_frame_is_refused_and_nothing_is_told_or_heard(
        self, link, participation, window_changes
    ):
        """CALL-57: With the setting off the switch frame is refused as unknown and no agent hears anyone."""
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        await guest.say(switch(agent.agent_id, on=True))

        assert [frame["reason"] for frame in guest.frames(SignalType.ERROR)] == [ErrorReason.INVALID_MESSAGE.value]
        assert hearing_frames(guest) == []
        assert open_hearing(participation, GUEST, guest.peer_id) is HearingRefusal.NOBODY_HEARS
        participation.heard(guest.peer_id, "hello", began_at=0.0)
        assert (lines_of(participation, GUEST, room_of(host)), window_changes) == ((), [])

    async def test_call_57_a_switch_turned_on_by_any_caller_records_nothing(
        self, link, participation, hearing, member_hearing
    ):
        """CALL-57: With the setting off no switch is ever turned on, whoever asks for it."""
        host, guest = await host_and_guest(link)
        agent = await dial(participation, guest)

        hearing.switch(agent, on=True)

        assert hearers(member_hearing, room_of(host), guest.peer_id) == set()
