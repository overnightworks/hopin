import asyncio

import pytest

from hopin.game.table import Line
from tests.game.support.fake_agent_backend import Holds, Succeeds
from tests.game.support.world import OPENING, build_world, cancelled

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.fixture
def world(sign_in_settings):
    return build_world(sign_in_settings())


async def test_cancelling_a_held_run_closes_its_generator(world):
    starter, _ = world.starter()
    held = Holds()
    world.backend.answer_next(held)
    run = asyncio.create_task(world.game_master.ask(starter, OPENING))
    await held.started.wait_async()

    await cancelled(run)

    [seen] = world.backend.runs
    assert seen.closed.is_set()


@pytest.mark.parametrize("end_of_the_first", ["finished", "cancelled"])
async def test_the_end_of_one_rooms_run_leaves_the_other_rooms_run_open(world, end_of_the_first):
    first, second = Holds(Succeeds("Erste Frage?")), Holds(Succeeds("Zweite Frage?"))
    world.backend.answer_next(first, second)
    first_starter, _ = world.starter("anna")
    second_starter, _ = world.starter("ben")
    first_run = asyncio.create_task(world.game_master.ask(first_starter, OPENING))
    await first.started.wait_async()
    second_run = asyncio.create_task(world.game_master.ask(second_starter, OPENING))
    await second.started.wait_async()

    if end_of_the_first == "finished":
        first.release.set()
        assert await first_run == Line("Erste Frage?")
    else:
        await cancelled(first_run)
    [first_seen, second_seen] = world.backend.runs
    assert (first_seen.closed.is_set(), second_seen.closed.is_set()) == (True, False)
    second.release.set()

    assert await second_run == Line("Zweite Frage?")
