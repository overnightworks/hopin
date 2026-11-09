import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from tests.support.dialed_call import serving_dialed_call
from tests.support.fake_issuer import FakeIssuer
from tests.support.signaling_fakes import RecordingFeature, WitnessFeatureBuilder

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.integration


@dataclass(eq=False)
class ShutdownWitness(RecordingFeature):
    """A call feature that runs `observe` when the call core shuts the features down."""

    observe: Callable[[], Awaitable[None]] | None = None

    async def shut_down(self):
        await self.observe()


def test_the_front_no_longer_serves_an_agent_when_participation_shuts_down(sign_in_settings):
    sign_in = sign_in_settings()
    served_then_not = []
    caller = {}

    async def observe():
        try:
            await asyncio.to_thread(caller["call"].request, caller["token"], "tools/list")
        except RuntimeError as failure:
            served_then_not.append(str(failure))

    with serving_dialed_call(
        sign_in,
        FakeIssuer(sign_in),
        more_call_features=(WitnessFeatureBuilder(lambda rooms: ShutdownWitness(rooms, observe=observe)),),
    ) as call:
        caller["call"], caller["token"] = call, call.agent_token(owner="owner-subject")
        assert call.request(caller["token"], "tools/list").status_code == 200

    assert ["the person front is not running" in message for message in served_then_not] == [True]
