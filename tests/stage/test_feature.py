import asyncio

import pytest

from hopin.config.constants import SESSION_RECHECK_SECONDS, SignalType
from hopin.signaling.frames import ForeignFrame
from hopin.stage.vocabulary import StageRefusal, StageSignal
from tests.signaling.support.browsers import HeldWrite, clear_sent
from tests.stage.support.tables import OTHER_TABLE_ID, OTHER_TABLE_LINK, SKAT, TABLE_ID, TABLE_LINK
from tests.support.signaling_fakes import account_named, text_frame

pytestmark = pytest.mark.integration

FIRST_ATTACHMENT = 1


def tables(browser):
    """The state frames the browser was sent, without their sequence numbers, which are the seat's own."""
    return [
        {key: value for key, value in frame.items() if key != "seq"}
        for frame in browser.frames(StageSignal.STAGE_TABLE)
    ]


def refusals(browser):
    return [frame["reason"] for frame in browser.frames(StageSignal.STAGE_REFUSED)]


async def attach(browser, link=TABLE_LINK):
    await browser.say({"type": StageSignal.STAGE_ATTACH.value, "link": link})


async def detach(browser, attachment):
    await browser.say({"type": StageSignal.STAGE_DETACH.value, "attachment": attachment})


async def host_opener_and_guest(link):
    host = await link.join("host")
    opener = await link.join("opener", host.room_id)
    guest = await link.join("guest", host.room_id)
    return host, opener, guest


@pytest.mark.asyncio
class TestAttach:
    async def test_every_seat_is_sent_the_table_with_its_opener(self, link):
        host, opener, guest = await host_opener_and_guest(link)

        await attach(opener)

        [shown] = tables(guest)[-1:]
        assert (shown["attachment"], shown["opener_id"]) == (shown["revision"], opener.peer_id)
        assert shown["src"].endswith("?mode=call")
        assert tables(host)[-1] == tables(opener)[-1] == shown

    async def test_the_table_frame_carries_the_attached_tables_id_and_none_without_a_table(self, link):
        host, opener, _guest = await host_opener_and_guest(link)
        assert tables(host)[-1]["table"] is None

        await attach(opener)
        assert tables(host)[-1]["table"] == TABLE_ID

        await detach(opener, tables(host)[-1]["attachment"])
        assert tables(host)[-1]["table"] is None

    async def test_the_table_frame_names_the_app_the_link_belongs_to_and_none_without_a_table(self, link):
        host, opener, _guest = await host_opener_and_guest(link)
        assert tables(host)[-1]["app"] is None

        await attach(opener)

        assert tables(host)[-1]["app"] == SKAT.name

    async def test_a_link_that_is_no_table_of_the_origin_is_refused_to_the_sender_alone(self, link):
        host, opener, _guest = await host_opener_and_guest(link)

        await attach(opener, "https://elsewhere.example/t/" + "a" * 32)

        assert refusals(opener) == [StageRefusal.BAD_LINK.value]
        assert refusals(host) == []
        assert tables(host)[-1]["attachment"] is None

    @pytest.mark.parametrize("link_value", [None, 7, ["x"]])
    async def test_a_link_that_is_no_string_is_an_invalid_message(self, link, link_value):
        host, opener, _guest = await host_opener_and_guest(link)

        await opener.say({"type": StageSignal.STAGE_ATTACH.value, "link": link_value})

        [error] = opener.frames(SignalType.ERROR)
        assert error["reason"] == "invalid_message"
        assert tables(host)[-1]["attachment"] is None

    async def test_a_second_attach_is_busy_and_changes_nothing(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await attach(guest, OTHER_TABLE_LINK)

        assert refusals(guest) == [StageRefusal.BUSY.value]
        assert tables(guest)[-1] == shown

    async def test_of_two_attaches_at_once_exactly_one_wins(self, link):
        host, opener, guest = await host_opener_and_guest(link)

        await asyncio.gather(attach(opener, TABLE_LINK), attach(guest, OTHER_TABLE_LINK))

        shown = tables(host)[-1]
        assert shown["src"] in (TABLE_LINK + "?mode=call", OTHER_TABLE_LINK + "?mode=call")
        assert sorted([refusals(opener), refusals(guest)]) == [[], [StageRefusal.BUSY.value]]
        assert shown["opener_id"] == (opener.peer_id if refusals(opener) == [] else guest.peer_id)


@pytest.mark.asyncio
class TestDetach:
    async def test_the_opener_removes_the_table_for_everyone_with_a_later_revision(self, link):
        host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await detach(opener, shown["attachment"])

        for browser in (host, opener, guest):
            gone = tables(browser)[-1]
            assert (gone["attachment"], gone["src"]) == (None, None)
            assert gone["revision"] > shown["revision"]

    async def test_a_seat_that_neither_opened_it_nor_hosts_is_refused_while_the_opener_is_there(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await detach(guest, shown["attachment"])

        assert refusals(guest) == [StageRefusal.NOT_OPENER_OR_HOST.value]
        assert tables(opener)[-1] == shown

    async def test_a_stale_detach_removes_nothing_newer(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        first = tables(guest)[-1]["attachment"]
        await detach(opener, first)
        await attach(guest, OTHER_TABLE_LINK)
        newer = tables(opener)[-1]

        await detach(opener, first)

        assert tables(opener)[-1] == newer
        assert refusals(opener) == []

    async def test_a_detach_without_a_table_is_ignored(self, link):
        _host, opener, _guest = await host_opener_and_guest(link)

        await detach(opener, 1)

        assert refusals(opener) == []

    @pytest.mark.parametrize("attachment", [None, "1", 1.5, True])
    async def test_an_attachment_that_is_no_integer_is_an_invalid_message(self, link, attachment):
        _host, opener, _guest = await host_opener_and_guest(link)

        await opener.say({"type": StageSignal.STAGE_DETACH.value, "attachment": attachment})

        [error] = opener.frames(SignalType.ERROR)
        assert error["reason"] == "invalid_message"

    async def test_an_attach_still_being_sent_reaches_a_later_seat_as_the_state_of_the_moment_it_is_sent(self, link):
        host, opener, guest = await host_opener_and_guest(link)
        clear_sent(host, opener, guest)
        held = HeldWrite(host.socket, first_only=True)

        attaching = asyncio.create_task(attach(opener))
        await held.reached()
        await detach(host, FIRST_ATTACHMENT)
        held.release.set()
        await attaching

        last = tables(guest)[-1]
        assert last["attachment"] is None
        assert last["revision"] == max(frame["revision"] for frame in tables(guest))


@pytest.mark.asyncio
class TestTheOpenerLeaves:
    async def test_an_opener_ending_during_its_attach_fan_out_leaves_every_seat_the_table_without_an_opener(
        self, link, scheduler
    ):
        host, opener, guest = await host_opener_and_guest(link)
        clear_sent(host, opener, guest)
        held = HeldWrite(host.socket, first_only=True)

        attaching = asyncio.create_task(attach(opener))
        await held.reached()
        opener.socket.account = None
        await scheduler.advance(SESSION_RECHECK_SECONDS)
        held.release.set()
        await attaching

        for seat in (guest, host):
            newest = max(tables(seat), key=lambda frame: frame["revision"])
            assert (newest["attachment"], newest["opener_id"]) == (FIRST_ATTACHMENT, None)
        assert tables(guest)[-1]["revision"] == max(frame["revision"] for frame in tables(guest))


@pytest.mark.asyncio
class TestFreshJoinsAndTheCallsEnd:
    async def test_a_seat_that_joins_is_sent_the_apps_it_may_show_by_name_and_title(self, link):
        host = await link.join("host")

        [apps] = host.frames(StageSignal.STAGE_APPS)

        assert apps["apps"] == [{"name": SKAT.name, "title": SKAT.title}]

    async def test_a_seat_that_joins_is_sent_that_nothing_is_shown(self, link):
        host = await link.join("host")

        late = await link.join("late", host.room_id)

        [shown] = tables(late)
        assert (shown["attachment"], shown["src"], shown["opener_id"]) == (None, None, None)

    async def test_a_seat_that_joins_is_sent_the_table_that_is_shown(self, link):
        host, opener, _guest = await host_opener_and_guest(link)
        await attach(opener)

        late = await link.join("late", host.room_id)

        assert tables(late)[-1] == tables(host)[-1]

    async def test_a_resumed_seat_is_sent_the_tables_it_missed(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await guest.drop()

        await attach(opener)
        await guest.resume()
        await guest.accept_resumption()

        assert tables(guest)[-1]["attachment"] is not None


@pytest.mark.asyncio
class TestSeatsOutsideARoom:
    async def test_a_socket_that_has_not_joined_can_attach_nothing(self, link, handler):
        socket = await link.open(account_named("stranger"))

        socket.push(text_frame({"type": StageSignal.STAGE_ATTACH.value, "link": TABLE_LINK, "seq": 1}))
        await link.settle(socket)

        assert socket.sent == []

    async def test_a_frame_of_another_type_is_none_of_skats(self, link, handler):
        host = await link.join("host")
        [feature] = handler.features

        assert await feature.on_frame(host.peer_id, ForeignFrame("something_else", {})) is False


@pytest.mark.asyncio
class TestTheCallsEnd:
    async def test_the_end_sends_no_further_table_state_into_the_ended_room(self, link):
        host, opener, _guest = await host_opener_and_guest(link)
        await attach(opener)
        shown_before = len(tables(opener))

        await host.say({"type": SignalType.END_ROOM.value})

        assert (len(tables(opener)), bool(opener.frames(SignalType.ROOM_CLOSED))) == (shown_before, True)

    async def test_a_seat_told_after_the_room_ended_is_sent_no_table(self, link, handler):
        host, opener, _guest = await host_opener_and_guest(link)
        await attach(opener)
        [feature] = handler.features

        feature.room_ended(host.room_id)
        await feature.seat_joined(host.peer_id)

        assert tables(host)[-1]["attachment"] is None


async def attach_named(browser, app=SKAT.name, context=TABLE_ID):
    await browser.say({"type": StageSignal.STAGE_ATTACH.value, "app": app, "context": context})


@pytest.mark.asyncio
class TestAttachByName:
    async def test_an_app_and_a_context_attach_the_same_table_as_its_link(self, link):
        host, opener, _guest = await host_opener_and_guest(link)

        await attach_named(opener)

        shown = tables(host)[-1]
        assert (shown["app"], shown["table"], shown["src"]) == (SKAT.name, TABLE_ID, TABLE_LINK + "?mode=call")

    @pytest.mark.parametrize(
        ("app", "context"), [("chess", TABLE_ID), (SKAT.name, "a/b"), (SKAT.name, ""), (SKAT.name, "a" * 65)]
    )
    async def test_an_unknown_app_or_a_context_outside_the_bound_is_refused(self, link, app, context):
        host, opener, _guest = await host_opener_and_guest(link)

        await attach_named(opener, app, context)

        assert refusals(opener) == [StageRefusal.BAD_LINK.value]
        assert tables(host)[-1]["attachment"] is None

    @pytest.mark.parametrize("fields", [{}, {"app": SKAT.name}, {"context": TABLE_ID}, {"app": 7, "context": TABLE_ID}])
    async def test_a_frame_naming_neither_a_link_nor_an_app_and_a_context_is_an_invalid_message(self, link, fields):
        _host, opener, _guest = await host_opener_and_guest(link)

        await opener.say({"type": StageSignal.STAGE_ATTACH.value, **fields})

        assert [error["reason"] for error in opener.frames(SignalType.ERROR)] == ["invalid_message"]


@pytest.mark.asyncio
class TestTheOpenCallShowingATable:
    async def test_a_call_that_attaches_a_table_is_the_call_showing_it_whether_it_used_a_link_or_a_name(
        self, link, stage
    ):
        first = await link.join("first")
        second = await link.join("second")
        assert stage.room_showing(SKAT.name, TABLE_ID) is None

        await attach(first)
        await attach_named(second, context=OTHER_TABLE_ID)

        assert stage.room_showing(SKAT.name, TABLE_ID) == first.room_id
        assert stage.room_showing(SKAT.name, OTHER_TABLE_ID) == second.room_id

    async def test_the_first_call_to_attach_a_table_keeps_it_when_a_second_shows_the_same_one(self, link, stage):
        first = await link.join("first")
        second = await link.join("second")

        await attach(first)
        await attach(second)

        assert stage.room_showing(SKAT.name, TABLE_ID) == first.room_id

    async def test_a_refused_attach_names_no_call(self, link, stage):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)

        await attach(guest, OTHER_TABLE_LINK)
        await attach(guest, "https://elsewhere.example/t/x")

        assert stage.room_showing(SKAT.name, OTHER_TABLE_ID) is None

    async def test_a_detach_frees_the_table_for_the_next_call_to_attach(self, link, stage):
        first = await link.join("first")
        second = await link.join("second")
        await attach(first)
        await attach(second)

        await detach(first, tables(first)[-1]["attachment"])
        assert stage.room_showing(SKAT.name, TABLE_ID) is None

        await attach(first)
        assert stage.room_showing(SKAT.name, TABLE_ID) == first.room_id

    async def test_the_detach_of_a_call_that_does_not_hold_the_table_leaves_the_winner_in_place(self, link, stage):
        winner = await link.join("winner")
        other = await link.join("other")
        await attach(winner)
        await attach(other)

        await detach(other, tables(other)[-1]["attachment"])

        assert stage.room_showing(SKAT.name, TABLE_ID) == winner.room_id

    async def test_the_end_of_the_call_frees_the_table(self, link, stage):
        host, opener, _guest = await host_opener_and_guest(link)
        await attach(opener)

        await host.say({"type": SignalType.END_ROOM.value})

        assert stage.room_showing(SKAT.name, TABLE_ID) is None

    async def test_the_end_of_another_call_leaves_the_winner_in_place(self, link, stage):
        winner = await link.join("winner")
        other = await link.join("other")
        await attach(winner)
        await attach(other)

        await other.say({"type": SignalType.END_ROOM.value})

        assert stage.room_showing(SKAT.name, TABLE_ID) == winner.room_id
