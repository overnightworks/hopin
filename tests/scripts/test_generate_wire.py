"""Tests for the generator that writes the browser's typed wire vocabulary."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import pytest

from hopin.config.constants import RoomClosedReason, SignalType
from hopin.signaling.frames import (
    AnswerRelayFrame,
    ChatBroadcastFrame,
    IceCandidateRelayFrame,
    MediaStateBroadcastFrame,
    OfferRelayFrame,
    ParticipantJoinedFrame,
    ParticipantLeftFrame,
    PeerId,
    RoomCreatedFrame,
    RoomLockedFrame,
    RoomUnlockedFrame,
)
from scripts import generate_wire

if TYPE_CHECKING:
    from pathlib import Path


def test_main_check_committed_wire_module_exits_clean() -> None:
    assert generate_wire.main(["--check"]) == 0


def test_render_core_vocabulary_names_enums_and_constants() -> None:
    rendered = generate_wire.render_core()

    assert 'export enum SIGNAL_TYPE {\n    OFFER = "offer",' in rendered
    assert "export enum ERROR_REASON {" in rendered
    assert "export enum ROOM_CLOSED_REASON {" in rendered
    assert "export type RoomClosedFrame = {" in rendered
    assert 'export const WEBSOCKET_PATH = "/ws";' in rendered
    assert "export const SESSION_ENDED_CLOSE_CODE = 4002;" in rendered
    assert "export const REPLAY_LOG_LIMIT = 256;" in rendered
    description_types = "export const DESCRIPTION_TYPES: readonly SIGNAL_TYPE[]"
    assert f"{description_types} = [SIGNAL_TYPE.ANSWER, SIGNAL_TYPE.OFFER];" in rendered
    assert "const" + " enum" not in rendered


def test_render_room_closed_frame_follows_the_frame_fields() -> None:
    @dataclass(frozen=True)
    class RoomClosedFrame:
        TYPE: ClassVar[SignalType] = SignalType.ROOM_CLOSED

        reason: RoomClosedReason
        wait_seconds: int | None = None

    assert generate_wire.render_frame_type(RoomClosedFrame, generate_wire.ROOM_CLOSED_FIELD_TYPES) == (
        "export type RoomClosedFrame = {\n"
        "    type: SIGNAL_TYPE.ROOM_CLOSED;\n"
        "    reason: ROOM_CLOSED_REASON;\n"
        "    wait_seconds?: number;\n"
        "};\n"
    )


def test_render_room_closed_frame_union_of_several_types_fails_generation() -> None:
    @dataclass(frozen=True)
    class RoomClosedFrame:
        TYPE: ClassVar[SignalType] = SignalType.ROOM_CLOSED

        wait_seconds: int | str | None = None

    with pytest.raises(TypeError, match="wait_seconds"):
        generate_wire.render_frame_type(RoomClosedFrame, generate_wire.ROOM_CLOSED_FIELD_TYPES)


def test_render_frame_type_unmapped_field_type_fails_generation() -> None:
    @dataclass(frozen=True)
    class RoomClosedFrame:
        TYPE: ClassVar[SignalType] = SignalType.ROOM_CLOSED

        wait_seconds: float

    with pytest.raises(TypeError, match="wait_seconds"):
        generate_wire.render_frame_type(RoomClosedFrame)


def test_render_frame_type_types_room_created_fields() -> None:
    assert generate_wire.render_frame_type(RoomCreatedFrame) == (
        "export type RoomCreatedFrame = {\n"
        "    type: SIGNAL_TYPE.ROOM_CREATED;\n"
        "    room_id: string;\n"
        "    is_host: boolean;\n"
        "    participant_id: string;\n"
        "    resume_token: string;\n"
        "};\n"
    )


def test_render_core_existing_participants_frame_types_its_containers() -> None:
    rendered = generate_wire.render_core()

    assert "    participants: string[];\n" in rendered
    assert "    names: Record<string, string>;\n" in rendered
    assert "    locked: boolean;\n" in rendered
    assert "export type ExistingParticipantsFrame = {\n    type: SIGNAL_TYPE.EXISTING_PARTICIPANTS;\n" in rendered


@pytest.mark.parametrize(
    ("frame_cls", "rendered"),
    [
        (
            ParticipantJoinedFrame,
            "export type ParticipantJoinedFrame = {\n"
            "    type: SIGNAL_TYPE.PARTICIPANT_JOINED;\n"
            "    participant_id: string;\n"
            "    name: string;\n"
            "};\n",
        ),
        (
            ParticipantLeftFrame,
            "export type ParticipantLeftFrame = {\n"
            "    type: SIGNAL_TYPE.PARTICIPANT_LEFT;\n"
            "    participant_id: string;\n"
            "};\n",
        ),
        (RoomLockedFrame, "export type RoomLockedFrame = {\n    type: SIGNAL_TYPE.ROOM_LOCKED;\n};\n"),
        (RoomUnlockedFrame, "export type RoomUnlockedFrame = {\n    type: SIGNAL_TYPE.ROOM_UNLOCKED;\n};\n"),
    ],
)
def test_render_frame_type_types_the_participant_and_lock_frames(frame_cls: type, rendered: str) -> None:
    assert generate_wire.render_frame_type(frame_cls) in generate_wire.render_core()
    assert generate_wire.render_frame_type(frame_cls) == rendered


@pytest.mark.parametrize(
    ("frame_cls", "extra_field_types", "rendered"),
    [
        (
            ChatBroadcastFrame,
            None,
            "export type ChatBroadcastFrame = {\n"
            "    type: SIGNAL_TYPE.CHAT;\n"
            "    message: string;\n"
            "    from: string;\n"
            "};\n",
        ),
        (
            MediaStateBroadcastFrame,
            None,
            "export type MediaStateBroadcastFrame = {\n"
            "    type: SIGNAL_TYPE.MEDIA_STATE;\n"
            "    from: string;\n"
            "    audio: boolean;\n"
            "    video: boolean;\n"
            "};\n",
        ),
        (
            OfferRelayFrame,
            generate_wire.RELAY_FIELD_TYPES,
            "export type OfferRelayFrame = {\n"
            "    type: SIGNAL_TYPE.OFFER;\n"
            "    sdp: unknown;\n"
            "    candidate: unknown;\n"
            "    from: string;\n"
            "};\n",
        ),
        (
            AnswerRelayFrame,
            generate_wire.RELAY_FIELD_TYPES,
            "export type AnswerRelayFrame = {\n"
            "    type: SIGNAL_TYPE.ANSWER;\n"
            "    sdp: unknown;\n"
            "    candidate: unknown;\n"
            "    from: string;\n"
            "};\n",
        ),
        (
            IceCandidateRelayFrame,
            generate_wire.RELAY_FIELD_TYPES,
            "export type IceCandidateRelayFrame = {\n"
            "    type: SIGNAL_TYPE.ICE_CANDIDATE;\n"
            "    sdp: unknown;\n"
            "    candidate: unknown;\n"
            "    from: string;\n"
            "};\n",
        ),
    ],
)
def test_render_frame_type_types_the_chat_media_state_and_relay_broadcasts(
    frame_cls: type, extra_field_types: dict[object, str] | None, rendered: str
) -> None:
    assert generate_wire.render_frame_type(frame_cls, extra_field_types) in generate_wire.render_core()
    assert generate_wire.render_frame_type(frame_cls, extra_field_types) == rendered


@pytest.mark.parametrize(
    ("frame", "extra_field_types"),
    [
        pytest.param(ChatBroadcastFrame(message="hi", from_=PeerId("p1")), None, id="chat"),
        pytest.param(MediaStateBroadcastFrame(from_=PeerId("p1"), audio=True, video=False), None, id="media_state"),
        pytest.param(
            OfferRelayFrame(sdp="s", candidate=None, from_=PeerId("p1")), generate_wire.RELAY_FIELD_TYPES, id="offer"
        ),
        pytest.param(
            AnswerRelayFrame(sdp="s", candidate=None, from_=PeerId("p1")), generate_wire.RELAY_FIELD_TYPES, id="answer"
        ),
        pytest.param(
            IceCandidateRelayFrame(sdp=None, candidate={"c": 1}, from_=PeerId("p1")),
            generate_wire.RELAY_FIELD_TYPES,
            id="ice_candidate",
        ),
    ],
)
def test_render_frame_type_names_the_properties_the_frame_sends_in_order(
    frame: ChatBroadcastFrame | MediaStateBroadcastFrame | OfferRelayFrame | AnswerRelayFrame | IceCandidateRelayFrame,
    extra_field_types: dict[object, str] | None,
) -> None:
    rendered = generate_wire.render_frame_type(type(frame), extra_field_types)
    properties = [line.split(":")[0].strip() for line in rendered.splitlines()[1:-1]]

    assert properties == list(frame.to_json())


def written_by_main(root: Path) -> dict[Path, str]:
    return {path: path.read_text() for path in generate_wire.outputs(root)}


def test_main_without_check_writes_every_rendered_file_under_the_root(tmp_path: Path) -> None:
    assert generate_wire.main([], tmp_path) == 0
    assert written_by_main(tmp_path) == generate_wire.outputs(tmp_path)


def test_main_check_in_sync_files_exits_clean_and_writes_nothing(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    before = written_by_main(tmp_path)

    assert generate_wire.main(["--check"], tmp_path) == 0
    assert written_by_main(tmp_path) == before


def test_main_check_drifted_file_exits_with_message(tmp_path: Path) -> None:
    generate_wire.main([], tmp_path)
    [drifted, *_] = generate_wire.outputs(tmp_path)
    drifted.write_text(drifted.read_text() + "// hand edit\n")

    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)


def test_main_check_missing_file_exits_with_message_and_creates_nothing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="out of date"):
        generate_wire.main(["--check"], tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_render_core_line_of_exactly_the_print_width_stays_on_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    one_line = next(
        line for line in generate_wire.render_core().splitlines() if line.startswith("export const DESCRIPTION_TYPES")
    )
    monkeypatch.setattr(generate_wire, "PRINT_WIDTH", len(one_line))

    assert f"\n{one_line}\n" in generate_wire.render_core()

    monkeypatch.setattr(generate_wire, "PRINT_WIDTH", len(one_line) - 1)

    assert f"\n{one_line}\n" not in generate_wire.render_core()
