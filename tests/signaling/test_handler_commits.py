"""The call core writes a browser frame's seq in the commit that applies the frame."""

from dataclasses import dataclass

import pytest
import pytest_asyncio

from hopin.config.constants import ConnectionOutcome, SignalType
from hopin.signaling.transition import InMemoryCallStore
from tests.signaling.support.browsers import Link, host_and_guest, note_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@dataclass(frozen=True)
class Commit:
    woken: tuple[str, ...]
    received: dict[str, int]
    """The seq each seat of the test had received after the commit; a seat the commit ended is absent."""


@pytest_asyncio.fixture
async def link(handler):
    link = Link(handler)
    yield link
    await link.close_all()


@pytest.fixture
def commits(monkeypatch):
    """Every commit the call store makes while the test runs, in order."""
    made = []
    commit = InMemoryCallStore.commit

    def recording_commit(store, view, change):
        woken = commit(store, view, change)
        seats = [seat for name in ("host", "guest") for seat in store.held_by(name)]
        made.append(Commit(woken, {peer_id: record.received_seq for peer_id, record in seats}))
        return woken

    monkeypatch.setattr(InMemoryCallStore, "commit", recording_commit)
    return made


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
