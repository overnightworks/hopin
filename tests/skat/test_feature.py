import asyncio

import pytest

from hopin.config.constants import SignalType
from hopin.signaling.frames import ForeignFrame
from hopin.skat.vocabulary import SkatRefusal, SkatSignal
from tests.signaling.support.browsers import HeldWrite, clear_sent
from tests.skat.support.tables import OTHER_TABLE_LINK, TABLE_LINK
from tests.support.signaling_fakes import account_named, text_frame

pytestmark = pytest.mark.integration


def tables(browser):
    """The state frames the browser was sent, without their sequence numbers, which are the seat's own."""
    return [
        {key: value for key, value in frame.items() if key != "seq"} for frame in browser.frames(SkatSignal.SKAT_TABLE)
    ]


def refusals(browser):
    return [frame["reason"] for frame in browser.frames(SkatSignal.SKAT_REFUSED)]


async def attach(browser, link=TABLE_LINK):
    await browser.say({"type": SkatSignal.SKAT_ATTACH.value, "link": link})


async def detach(browser, attachment):
    await browser.say({"type": SkatSignal.SKAT_DETACH.value, "attachment": attachment})


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

    async def test_a_link_that_is_no_table_of_the_origin_is_refused_to_the_sender_alone(self, link):
        host, opener, _guest = await host_opener_and_guest(link)

        await attach(opener, "https://elsewhere.example/t/" + "a" * 32)

        assert refusals(opener) == [SkatRefusal.BAD_LINK.value]
        assert refusals(host) == []
        assert tables(host)[-1]["attachment"] is None

    @pytest.mark.parametrize("link_value", [None, 7, ["x"]])
    async def test_a_link_that_is_no_string_is_an_invalid_message(self, link, link_value):
        host, opener, _guest = await host_opener_and_guest(link)

        await opener.say({"type": SkatSignal.SKAT_ATTACH.value, "link": link_value})

        [error] = opener.frames(SignalType.ERROR)
        assert error["reason"] == "invalid_message"
        assert tables(host)[-1]["attachment"] is None

    async def test_a_second_attach_is_busy_and_changes_nothing(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await attach(guest, OTHER_TABLE_LINK)

        assert refusals(guest) == [SkatRefusal.BUSY.value]
        assert tables(guest)[-1] == shown

    async def test_of_two_attaches_at_once_exactly_one_wins(self, link):
        host, opener, guest = await host_opener_and_guest(link)

        await asyncio.gather(attach(opener, TABLE_LINK), attach(guest, OTHER_TABLE_LINK))

        shown = tables(host)[-1]
        assert shown["src"] in (TABLE_LINK + "?mode=call", OTHER_TABLE_LINK + "?mode=call")
        assert sorted([refusals(opener), refusals(guest)]) == [[], [SkatRefusal.BUSY.value]]
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

    async def test_the_host_removes_a_table_another_seat_opened(self, link):
        host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)

        await detach(host, tables(guest)[-1]["attachment"])

        assert tables(guest)[-1]["attachment"] is None

    async def test_a_seat_that_neither_opened_it_nor_hosts_is_refused_while_the_opener_is_there(self, link):
        _host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await detach(guest, shown["attachment"])

        assert refusals(guest) == [SkatRefusal.NOT_OPENER_OR_HOST.value]
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

        await opener.say({"type": SkatSignal.SKAT_DETACH.value, "attachment": attachment})

        [error] = opener.frames(SignalType.ERROR)
        assert error["reason"] == "invalid_message"


@pytest.mark.asyncio
class TestTheOpenerLeaves:
    async def test_the_table_stays_and_anyone_may_remove_it_afterwards(self, link):
        host, opener, guest = await host_opener_and_guest(link)
        await attach(opener)
        shown = tables(guest)[-1]

        await opener.drop(1000)

        stays = tables(guest)[-1]
        assert (stays["attachment"], stays["opener_id"]) == (shown["attachment"], None)
        assert stays["revision"] > shown["revision"]
        await detach(guest, shown["attachment"])
        assert tables(host)[-1]["attachment"] is None

    async def test_an_attach_still_being_sent_ends_on_the_newer_state_without_an_opener(self, link):
        host, opener, guest = await host_opener_and_guest(link)
        clear_sent(host, opener, guest)
        held = HeldWrite(guest.socket, first_only=True)

        attaching = asyncio.create_task(attach(opener))
        await held.reached()
        leaving = asyncio.create_task(opener.drop(1000))
        await asyncio.sleep(0)  # the opener's close is read; the seat's change is applied before anything is awaited
        held.release.set()
        await asyncio.gather(attaching, leaving)

        last = tables(guest)[-1]
        assert (last["attachment"] is not None, last["opener_id"]) == (True, None)
        revisions = [frame["revision"] for frame in tables(guest)]
        assert last["revision"] == max(revisions)


@pytest.mark.asyncio
class TestFreshJoinsAndTheCallsEnd:
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

        socket.push(text_frame({"type": SkatSignal.SKAT_ATTACH.value, "link": TABLE_LINK, "seq": 1}))
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
