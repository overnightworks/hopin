"""The agent's side of CALL-60 for the participation tests: a fake renderer and the calls that speak and wait."""

import asyncio

from hopin.participation.speaking import RenderedSentence
from hopin.participation.vocabulary import ParticipationSignal, VoiceFailure
from tests.participation.support.people import person_named

GUEST = person_named("guest")
SENTENCE_MS = 1500
LONG_MS = 60 * 60 * 1000


def rendering(wav=b"sound", duration_ms=SENTENCE_MS):
    async def render(text, voice):
        return RenderedSentence(wav, duration_ms)

    return render


async def speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id, offered=None):
    """The agent's answer once the sentence started and the announcement of it that follows the answer was sent."""
    render = rendering() if render is None else render
    known = asyncio.all_tasks()

    async def render_and_note_the_running_tasks(sentence_text, voice):
        try:
            return await render(sentence_text, voice)
        finally:
            known.update(asyncio.all_tasks())

    answer = await participation.speak(who, room_id, text, render_and_note_the_running_tasks, offered)
    announcements = asyncio.all_tasks() - known - {asyncio.current_task()}
    if announcements:
        await asyncio.wait(announcements)
    return answer


async def waiting_to_speak(participation, text="Guten Tag", render=None, *, who=GUEST, room_id):
    """A `speak` waiting in the agent's queue for its sentence to start; the task holds its answer."""
    answer = asyncio.create_task(participation.speak(who, room_id, text, rendering() if render is None else render))
    await asyncio.sleep(0)
    return answer


async def answers_so_far(scheduler, seconds, *answers):
    """Which of the answers are in after the clock moved on by `seconds`."""
    await scheduler.advance(seconds)
    await asyncio.sleep(0)
    return [answer.done() for answer in answers]


LONG = rendering(duration_ms=LONG_MS)


async def renders_nothing(sentence, voice):
    return None


async def renders_failing(sentence, voice):
    return VoiceFailure.BUSY


async def renders_cancelled(sentence, voice):
    raise asyncio.CancelledError


async def renders_broken(sentence, voice):
    raise RuntimeError("the speech service broke")


async def removed_by_the_host(participation, host, guest, agent):
    await host.say({"type": ParticipationSignal.AGENT_REMOVE.value, "agent_id": agent.agent_id})
