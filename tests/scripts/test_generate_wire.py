"""Tests for the generator that writes the browser's typed wire vocabulary."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Literal

import pytest

from hopin.config.constants import RoomClosedReason, SignalType
from hopin.participation.vocabulary import (
    HEARING_PATH,
    LOOK_MAX_BYTES,
    LOOK_MAX_PIXELS,
    LOOK_PART_CHARACTERS,
    HearingClose,
)
from hopin.signaling.frames import (
    SEQUENCE_KEY,
    AnswerRelayFrame,
    ChatBroadcastFrame,
    ErrorFrame,
    IceCandidateRelayFrame,
    MediaStateBroadcastFrame,
    OfferRelayFrame,
    ParticipantJoinedFrame,
    ParticipantLeftFrame,
    ResumedFrame,
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


class FeatureSignal(StrEnum):
    MOVED = "moved"
    STOPPED = "stopped"


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"
    OFF = "off"


FEATURE_ENUMS = {"FEATURE_SIGNAL": FeatureSignal, "MODE": Mode}


def test_render_frame_type_renders_enums_and_literal_members() -> None:
    @dataclass(frozen=True)
    class MovedFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

        mode: Mode
        fixed: Literal[Mode.OFF]
        either: Literal[Mode.FAST, Mode.SLOW]
        flag: Literal[True] | None = None

    rendered = generate_wire.render_frame_type(MovedFrame, None, FEATURE_ENUMS)

    assert rendered == (
        "export type MovedFrame = {\n"
        "    type: FEATURE_SIGNAL.MOVED;\n"
        "    mode: MODE;\n"
        "    fixed: MODE.OFF;\n"
        "    either: MODE.FAST | MODE.SLOW;\n"
        "    flag?: true;\n"
        "};\n"
    )


def test_render_frame_type_renders_a_field_without_a_default_as_present_and_nullable() -> None:
    @dataclass(frozen=True)
    class HeldFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

        holder: str | None
        number: int
        nothing: None
        sometimes: str | None = None

    rendered = generate_wire.render_frame_type(HeldFrame, None, FEATURE_ENUMS)

    assert rendered == (
        "export type HeldFrame = {\n"
        "    type: FEATURE_SIGNAL.MOVED;\n"
        "    holder: string | null;\n"
        "    number: number;\n"
        "    nothing: null;\n"
        "    sometimes?: string;\n"
        "};\n"
    )


def test_render_frame_type_wraps_a_union_wider_than_the_print_width_one_member_per_line() -> None:
    @dataclass(frozen=True)
    class MovedFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

        wide: Literal[Mode.FAST, Mode.SLOW, Mode.OFF]

    exported = "AN_EXPORTED_ENUM_NAME_OF_FORTY_CHARACTERS"
    rendered = generate_wire.render_frame_type(MovedFrame, None, {"FEATURE_SIGNAL": FeatureSignal, exported: Mode})

    assert rendered == (
        "export type MovedFrame = {\n"
        "    type: FEATURE_SIGNAL.MOVED;\n"
        "    wide:\n"
        f"        | {exported}.FAST\n"
        f"        | {exported}.SLOW\n"
        f"        | {exported}.OFF;\n"
        "};\n"
    )


def test_render_frame_type_unexported_enum_fails_generation() -> None:
    @dataclass(frozen=True)
    class MovedFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

        mode: Literal[Mode.OFF]

    with pytest.raises(TypeError, match="Mode"):
        generate_wire.render_frame_type(MovedFrame, None, {"FEATURE_SIGNAL": FeatureSignal})


def test_render_feature_maps_each_signal_to_its_frame_or_the_union_of_its_frames() -> None:
    @dataclass(frozen=True)
    class MovedFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

    @dataclass(frozen=True)
    class FarFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.MOVED

    @dataclass(frozen=True)
    class StoppedFrame:
        TYPE: ClassVar[FeatureSignal] = FeatureSignal.STOPPED

    rendered = generate_wire.render_feature(FEATURE_ENUMS, [MovedFrame, FarFrame, StoppedFrame])

    assert rendered.endswith(
        "export type FeatureFrameByType = {\n"
        "    [FEATURE_SIGNAL.MOVED]: MovedFrame | FarFrame;\n"
        "    [FEATURE_SIGNAL.STOPPED]: StoppedFrame;\n"
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
        (
            ResumedFrame,
            "export type ResumedFrame = {\n"
            "    type: SIGNAL_TYPE.RESUMED;\n"
            "    resume_token: string;\n"
            "    last_seq: number;\n"
            "};\n",
        ),
    ],
)
def test_render_frame_type_types_the_participant_lock_and_resumed_frames(frame_cls: type, rendered: str) -> None:
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
            ErrorFrame,
            generate_wire.ERROR_FIELD_TYPES,
            "export type ErrorFrame = {\n"
            "    type: SIGNAL_TYPE.ERROR;\n"
            "    reason: ERROR_REASON;\n"
            "    message: string;\n"
            "    capacity?: number;\n"
            "    limit?: number;\n"
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
def test_render_frame_type_types_the_error_chat_media_state_and_relay_frames(
    frame_cls: type, extra_field_types: dict[object, str] | None, rendered: str
) -> None:
    assert generate_wire.render_frame_type(frame_cls, extra_field_types) in generate_wire.render_core()
    assert generate_wire.render_frame_type(frame_cls, extra_field_types) == rendered


def participation_wire(root: Path) -> str:
    return generate_wire.outputs(root)[root / "frontend/src/lib/features/participation/wire.ts"]


def test_participation_wire_renders_the_hearing_close_codes_as_numbers(tmp_path: Path) -> None:
    rendered = participation_wire(tmp_path)

    assert "export enum HEARING_CLOSE {\n" in rendered
    assert all(f"    {name} = {member.value},\n" in rendered for name, member in HearingClose.__members__.items())


def test_participation_wire_carries_the_hearing_path(tmp_path: Path) -> None:
    assert f'export const HEARING_PATH = "{HEARING_PATH}";\n' in participation_wire(tmp_path)


def test_participation_wire_carries_the_bounds_of_a_look(tmp_path: Path) -> None:
    rendered = participation_wire(tmp_path)

    assert f"export const LOOK_MAX_PIXELS = {LOOK_MAX_PIXELS};\n" in rendered
    assert f"export const LOOK_MAX_BYTES = {LOOK_MAX_BYTES};\n" in rendered
    assert f"export const LOOK_PART_CHARACTERS = {LOOK_PART_CHARACTERS};\n" in rendered


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


def test_render_core_server_frame_map_has_one_entry_per_rendered_frame() -> None:
    rendered = generate_wire.render_core()
    frames = re.findall(r"export type (\w+) = \{\n    type: SIGNAL_TYPE\.(\w+);", rendered)
    map_body = rendered.split("export type ServerFrameByType = {\n", 1)[1].split("};\n", 1)[0]
    entries = re.findall(r"    \[SIGNAL_TYPE\.(\w+)\]: (\w+);\n", map_body)

    assert frames
    assert sorted(entries) == sorted((signal, frame) for frame, signal in frames)
    assert map_body.count("\n") == len(frames)


def test_render_core_server_frame_union_is_the_value_union_of_the_map() -> None:
    assert "export type ServerFrame = ServerFrameByType[keyof ServerFrameByType];\n" in generate_wire.render_core()


def test_render_core_sequenced_frame_adds_the_replay_sequence_key_as_optional() -> None:
    assert f"export type SequencedFrame = ServerFrame & {{ {SEQUENCE_KEY}?: number }};\n" in generate_wire.render_core()


@dataclass(frozen=True)
class NestedRow:
    key: str
    pending: bool


@dataclass(frozen=True)
class RowsFrame:
    TYPE: ClassVar[SignalType] = SignalType.ROOM_CREATED

    rows: list[NestedRow]


def test_render_frame_type_renders_a_list_of_records_as_an_array_of_their_interface() -> None:
    assert generate_wire.render_frame_type(RowsFrame) == (
        "export type RowsFrame = {\n    type: SIGNAL_TYPE.ROOM_CREATED;\n    rows: NestedRow[];\n};\n"
    )


def test_render_record_type_renders_the_record_as_its_own_interface() -> None:
    assert generate_wire.render_record_type(NestedRow) == (
        "export interface NestedRow {\n    key: string;\n    pending: boolean;\n}\n"
    )


def test_render_feature_writes_each_nested_record_once_before_the_frames_that_hold_them() -> None:
    @dataclass(frozen=True)
    class OtherRowsFrame:
        TYPE: ClassVar[SignalType] = SignalType.ROOM_LOCKED

        rows: list[NestedRow]

    rendered = generate_wire.render_feature({"SIGNAL_TYPE": SignalType}, [RowsFrame, OtherRowsFrame])

    assert rendered.count("export interface NestedRow {") == 1
    assert rendered.index("export interface NestedRow") < rendered.index("export type RowsFrame")


def test_render_feature_ends_with_the_constants_it_is_given() -> None:
    rendered = generate_wire.render_feature({"SIGNAL_TYPE": SignalType}, [RowsFrame], "export const LIMIT = 3;\n")

    assert rendered.endswith("};\n\nexport const LIMIT = 3;\n")
