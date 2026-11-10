"""The figures the route serves: one fetch per digest at a time, on two workers of their own, kept least recently
used within the cache's bound."""

import asyncio
import threading
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
import structlog.testing

from hopin.participation.figures import FigureStore, NamedFigure
from hopin.participation.vocabulary import FIGURE_CACHE_BYTES, FIGURE_FETCH_WORKERS, FIGURE_MAX_BYTES, FigureRefusal
from tests.participation.support.figures import WAIT_SECONDS, figure_url

pytestmark = pytest.mark.asyncio

ROOM = "room-of-the-agent"


def digest(number: int) -> str:
    return f"{number:064x}"


def named(*urls: str, room_id: str = ROOM) -> tuple[NamedFigure, ...]:
    return tuple(NamedFigure(room_id, url) for url in urls)


@dataclass
class Fetches:
    """The fetch the store runs: each URL answers its outcome, the bytes `FILE` by default; a held URL waits for its
    release once its start is set."""

    outcomes: dict[str, bytes | FigureRefusal] = field(default_factory=dict)
    held: dict[str, tuple[threading.Event, threading.Event]] = field(default_factory=dict)
    fetched: list[str] = field(default_factory=list)
    threads: list[str] = field(default_factory=list)

    def hold(self, url):
        self.held[url] = (threading.Event(), threading.Event())
        return self.held[url]

    def __call__(self, figure):
        self.fetched.append(figure.url)
        self.threads.append(threading.current_thread().name)
        if figure.url in self.held:
            started, release = self.held[figure.url]
            started.set()
            assert release.wait(WAIT_SECONDS)
        return self.outcomes.get(figure.url, f"bytes of {figure.url}".encode())


@pytest.fixture
def fetches():
    return Fetches()


@pytest_asyncio.fixture
async def store(fetches):
    store = FigureStore(fetches)
    yield store
    for _, release in fetches.held.values():
        release.set()
    store.shut_down()


async def started(event):
    assert await asyncio.to_thread(event.wait, WAIT_SECONDS)


class TestOneFetchPerDigest:
    async def test_concurrent_reads_of_one_digest_share_one_fetch(self, store, fetches):
        url = figure_url()
        fetch_started, release = fetches.hold(url)
        reads = [asyncio.create_task(store.bytes_of(digest(1), named(url))) for _ in range(3)]
        await started(fetch_started)

        release.set()

        assert await asyncio.gather(*reads) == [f"bytes of {url}".encode()] * 3
        assert fetches.fetched == [url]

    async def test_a_verified_file_is_served_from_the_cache_without_another_fetch(self, store, fetches):
        url = figure_url()
        first = await store.bytes_of(digest(1), named(url))

        assert (await store.bytes_of(digest(1), named(url)), fetches.fetched) == (first, [url])

    async def test_the_urls_are_tried_in_order_until_one_verifies_so_a_failing_one_denies_nothing(self, store, fetches):
        failing, good = figure_url("failing.glb"), figure_url("good.glb")
        fetches.outcomes[failing] = FigureRefusal.MISMATCH

        with structlog.testing.capture_logs() as logs:
            served = await store.bytes_of(digest(1), named(failing, good))

        assert (served, fetches.fetched) == (f"bytes of {good}".encode(), [failing, good])
        assert logs == [
            {"event": "agent_figure_fetch_failed", "room_id": ROOM, "reason": "mismatch", "log_level": "info"}
        ]

    async def test_a_failed_fetch_is_answered_with_nothing_and_tried_again_on_the_next_read(self, store, fetches):
        url = figure_url()
        fetches.outcomes[url] = FigureRefusal.UNREACHABLE

        first = await store.bytes_of(digest(1), named(url))
        second = await store.bytes_of(digest(1), named(url))

        assert (first, second, fetches.fetched) == (None, None, [url, url])


class TestTheCache:
    async def test_past_its_bound_the_least_recently_used_file_goes_and_is_fetched_again_when_read(
        self, store, fetches
    ):
        fits = FIGURE_CACHE_BYTES // FIGURE_MAX_BYTES
        urls = [figure_url(f"figure-{number}.glb") for number in range(fits + 1)]
        for url in urls:
            fetches.outcomes[url] = bytes(FIGURE_MAX_BYTES)
        for number, url in enumerate(urls[:fits]):
            await store.bytes_of(digest(number), named(url))
        await store.bytes_of(digest(0), named(urls[0]))

        await store.bytes_of(digest(fits), named(urls[fits]))
        await store.bytes_of(digest(0), named(urls[0]))
        await store.bytes_of(digest(1), named(urls[1]))

        assert fetches.fetched == [*urls, urls[1]]


class TestTheWorkers:
    async def test_two_fetches_run_and_a_third_waits_while_the_default_executor_stays_free(self, store, fetches):
        urls = [figure_url(f"stalled-{number}.glb") for number in range(FIGURE_FETCH_WORKERS + 1)]
        holds = [fetches.hold(url) for url in urls]
        reads = [asyncio.create_task(store.bytes_of(digest(number), named(url))) for number, url in enumerate(urls)]
        for fetch_started, _ in holds[:FIGURE_FETCH_WORKERS]:
            await started(fetch_started)

        free = await asyncio.get_running_loop().run_in_executor(None, lambda: "free")
        waiting = not holds[FIGURE_FETCH_WORKERS][0].is_set()
        holds[0][1].set()
        await started(holds[FIGURE_FETCH_WORKERS][0])
        for _, release in holds:
            release.set()
        await asyncio.gather(*reads)

        assert (free, waiting) == ("free", True)
        assert len(set(fetches.threads)) == FIGURE_FETCH_WORKERS
