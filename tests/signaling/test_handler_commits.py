"""The call core writes a browser frame's seq in the commit that applies the frame."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import pytest_asyncio

from hopin.config.constants import ConnectionOutcome, SignalType
from hopin.errors import HopInError
from hopin.signaling.transition import Calls
from tests.signaling.support.browsers import Hold, Link, host_and_guest, note_for
from tests.support.signaling_fakes import text_frame

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass(frozen=True)
class Commit:
    woken: tuple[str, ...]
    received: dict[str, int]
    """The seq each seat of the test had received after the commit; a seat the commit ended is absent."""
    refused: bool = False


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


@pytest.fixture
def commits(monkeypatch):
    """Every commit the call store makes while the test runs, in order."""
    made = []
    commit = Calls.commit

    async def recording_commit(store, view, change):
        woken = ()
        refused = False
        try:
            woken = await commit(store, view, change)
        except HopInError:
            refused = True
            raise
        finally:
            seats = [seat for name in ("host", "guest") for seat in await store.store.held_by(name)]
            made.append(Commit(woken, {peer_id: record.received_seq for peer_id, record in seats}, refused))
        return woken

    monkeypatch.setattr(Calls, "commit", recording_commit)
    return made


@pytest.fixture
def held_refusal(commits, monkeypatch):
    """Sends the guest a relay to nobody and returns once the server holds at its refusal log, commits cleared."""

    async def send_held_relay(host, guest):
        refusal_log = Hold()

        async def held_log(event, **_fields):
            if event == "relay.refused":
                await refusal_log.hold()

        monkeypatch.setattr("hopin.signaling.routing.logger", SimpleNamespace(ainfo=held_log))
        guest.sent_count += 1
        guest.socket.push(text_frame({**note_for(host, "hi"), "target": "nobody", "seq": guest.sent_count}))
        await refusal_log.reached()
        commits.clear()
        return refusal_log

    return send_held_relay


def relay(host, guest):
    return guest, note_for(host, "hi"), (host,)


def media_state(host, guest):
    return guest, {"type": SignalType.MEDIA_STATE.value, "audio": True, "video": False}, (host,)


def lock(host, guest):
    return host, {"type": SignalType.LOCK_ROOM.value}, (host, guest)


def end(host, guest):
    return host, {"type": SignalType.END_ROOM.value}, (host, guest)


def refused_join(host, guest):
    return guest, {"type": SignalType.JOIN.value, "room_id": host.room_id}, (guest,)


def unrelated_relay(host, guest):
    return guest, {**note_for(host, "hi"), "target": "nobody"}, ()


def connection_outcome(host, guest):
    return guest, {"type": SignalType.CONNECTION_OUTCOME.value, "outcome": ConnectionOutcome.DIRECT.value}, ()


class TestFrameSequence:
    @pytest.mark.parametrize(
        "action", [relay, media_state, lock, end, refused_join, unrelated_relay, connection_outcome]
    )
    async def test_the_first_commit_after_a_frame_counts_it_received_and_carries_its_frames(
        self, link, commits, action
    ):
        host, guest = await host_and_guest(link)
        sender, frame, woken = action(host, guest)
        commits.clear()

        await sender.say(frame)

        [first, *_] = commits
        assert first.received[sender.peer_id] == sender.sent_count
        assert first.woken == tuple(browser.peer_id for browser in woken)

    async def test_a_refused_edit_leaves_the_seq_to_the_commit_that_tells_of_the_refusal(self, link, commits):
        _host, guest = await host_and_guest(link)
        commits.clear()

        await guest.say({"type": SignalType.LOCK_ROOM.value})

        refusal, telling = commits
        assert refusal.refused
        assert refusal.received[guest.peer_id] == guest.sent_count - 1
        assert telling.received[guest.peer_id] == guest.sent_count
        assert telling.woken == (guest.peer_id,)

    async def test_another_seats_commit_leaves_the_seq_of_a_frame_that_changes_nothing_to_its_own_empty_commit(
        self, link, commits, held_refusal
    ):
        host, guest = await host_and_guest(link)
        refusal_log = await held_refusal(host, guest)

        await host.say({"type": SignalType.MEDIA_STATE.value, "audio": True, "video": False})
        refusal_log.release.set()
        await link.settle(guest.socket)

        during, *after = commits
        assert during.received[guest.peer_id] == guest.sent_count - 1
        assert after[-1].received[guest.peer_id] == guest.sent_count

    async def test_a_frame_that_changes_nothing_is_dropped_with_its_seat_when_the_call_ends_meanwhile(
        self, link, commits, held_refusal
    ):
        host, guest = await host_and_guest(link)
        refusal_log = await held_refusal(host, guest)

        await host.say({"type": SignalType.END_ROOM.value})
        refusal_log.release.set()
        await link.settle(guest.socket)

        assert guest.peer_id not in commits[-1].received
