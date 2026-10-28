import pytest

from hopin.config.constants import ErrorReason, SignalType
from hopin.game.vocabulary import GameSignal

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest.mark.parametrize(
    "signal_type",
    [
        pytest.param(GameSignal.GAME_LINE.value, id="a game type only the server sends"),
        pytest.param(GameSignal.GAME_ANSWER_COUNT.value, id="another game type only the server sends"),
        pytest.param("unknown", id="a type nobody knows"),
    ],
)
async def test_a_frame_the_game_does_not_take_is_refused_as_an_unknown_signal_type(link, game_master, signal_type):
    host = await link.join("host")

    await host.say({"type": signal_type})

    [refusal] = host.frames(SignalType.ERROR)
    assert (refusal["reason"], refusal["message"]) == (
        ErrorReason.INVALID_MESSAGE.value,
        f"Unknown signal type: {signal_type}",
    )
    assert game_master.calls == []
