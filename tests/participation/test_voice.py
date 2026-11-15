"""CALL-60: the sound of a sentence grows as its chunks arrive and every reader gets it from its start."""

import asyncio

import pytest

from hopin.participation.voice import VoiceBrokenError, VoiceStore

pytestmark = pytest.mark.asyncio

ROOM = "room"
SPEAKER = "agent"


async def reading_next(reader):
    """The reader's next read, started and left waiting."""
    task = asyncio.ensure_future(anext(reader))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return task


async def everything(reader):
    return [chunk async for chunk in reader]


@pytest.fixture
def voice():
    return VoiceStore().open(ROOM, SPEAKER)


class TestAGrowingVoice:
    async def test_call_60_two_readers_each_get_every_chunk_as_it_arrives(self, voice):
        first, second = voice.read(), voice.read()
        voice.append(b"one")
        voice.append(b"two")
        voice.finish()

        assert (await everything(first), await everything(second)) == ([b"one", b"two"], [b"one", b"two"])

    async def test_call_60_a_reader_waits_for_the_reading_next_and_gets_it_when_it_arrives(self, voice):
        voice.append(b"one")
        reader = voice.read()
        assert await anext(reader) == b"one"

        waiting = await reading_next(reader)
        before = waiting.done()
        voice.append(b"two")

        assert (before, await waiting) == (False, b"two")

    async def test_call_60_a_reader_that_comes_late_gets_the_sound_from_its_start(self, voice):
        voice.append(b"one")
        voice.append(b"two")

        late = voice.read()

        assert await anext(late) == b"one"

    async def test_call_60_a_reader_ends_when_the_sound_is_finished(self, voice):
        voice.append(b"one")
        reader = voice.read()
        assert await anext(reader) == b"one"
        waiting = await reading_next(reader)

        voice.finish()

        with pytest.raises(StopAsyncIteration):
            await waiting

    async def test_call_60_a_reader_of_a_finished_sound_gets_all_of_it_and_ends(self, voice):
        voice.append(b"one")
        voice.finish()

        assert await everything(voice.read()) == [b"one"]

    async def test_call_61_a_reader_fails_where_the_sound_broke_off_after_the_chunks_it_got(self, voice):
        voice.append(b"one")
        reader = voice.read()
        assert await anext(reader) == b"one"
        waiting = await reading_next(reader)

        voice.break_off()

        with pytest.raises(VoiceBrokenError):
            await waiting

    async def test_call_61_a_reader_that_comes_after_the_break_gets_the_chunks_and_then_the_failure(self, voice):
        voice.append(b"one")
        voice.break_off()
        late = voice.read()

        assert await anext(late) == b"one"
        with pytest.raises(VoiceBrokenError):
            await anext(late)

    async def test_call_60_a_chunk_after_the_end_is_not_kept(self, voice):
        voice.append(b"one")
        voice.finish()
        voice.append(b"late")

        assert await everything(voice.read()) == [b"one"]

    async def test_call_60_a_finished_sound_stays_whole_when_it_is_broken_off_after_it(self, voice):
        voice.append(b"one")
        voice.finish()
        voice.break_off()

        assert await everything(voice.read()) == [b"one"]


class TestTheStore:
    async def test_call_60_a_whole_sound_is_kept_under_its_id_and_its_room(self):
        store = VoiceStore()

        voice = store.put(ROOM, SPEAKER, b"whole")

        assert (store.get(voice.id), store.room_of(voice.id), await everything(voice.read())) == (
            voice,
            ROOM,
            [b"whole"],
        )

    async def test_call_60_a_released_sound_is_gone_and_its_reader_ends_where_the_sound_stood(self):
        store = VoiceStore()
        voice = store.open(ROOM, SPEAKER)
        voice.append(b"one")
        reader = voice.read()
        assert await anext(reader) == b"one"
        waiting = await reading_next(reader)

        store.release(voice.id)

        with pytest.raises(StopAsyncIteration):
            await waiting
        assert (store.get(voice.id), store.room_of(voice.id)) == (None, None)

    async def test_call_60_a_speaker_keeps_only_its_latest_sounds_and_the_others_keep_theirs(self):
        store = VoiceStore()
        kept = [
            store.put(ROOM, SPEAKER, b"first"),
            store.put(ROOM, SPEAKER, b"second"),
            store.put(ROOM, SPEAKER, b"third"),
        ]
        other = store.put(ROOM, "someone else", b"other")

        store.keep_latest(ROOM, SPEAKER, 2)

        assert ([store.get(voice.id) for voice in kept], store.get(other.id)) == ([None, kept[1], kept[2]], other)

    async def test_call_60_a_speaker_that_goes_loses_every_sound_it_kept_in_the_room(self):
        store = VoiceStore()
        kept = [store.open(ROOM, SPEAKER), store.put(ROOM, SPEAKER, b"whole")]
        other = store.open(ROOM, "someone else")

        store.forget_speaker(ROOM, SPEAKER)

        assert ([store.get(voice.id) for voice in kept], store.get(other.id)) == ([None, None], other)
