import json
from dataclasses import MISSING, Field, dataclass, fields
from enum import Enum, StrEnum
from typing import TYPE_CHECKING, ClassVar, NewType, TypeGuard, cast

from hopin.config.constants import (
    MAX_SIGNALING_FRAME_LENGTH,
    ErrorReason,
    ResumeRefusalCause,
    RoomClosedReason,
    SignalType,
)
from hopin.errors import HopInError, InvalidMessageError, ResumeRefusedError

if TYPE_CHECKING:
    from collections.abc import Callable

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]

SEQUENCE_KEY = "seq"
SENDER_KEY = "from"
TYPE_KEY = "type"

PeerId = NewType("PeerId", str)


def _is_sequence_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


# The wire key of a field whose Python name is not the key, because the name is a keyword.
WIRE_FIELD_NAMES = {"from_": SENDER_KEY}


def is_omitted_when_none(field: Field[object]) -> bool:
    """A field with a default is absent from the wire when None; one without a default is always sent."""
    return field.default is not MISSING


def _wire_value(value: object) -> JsonValue:
    """The JSON value of a field value: an enum by its value, a container and a nested record as fresh copies."""
    match value:
        case Enum():
            return cast("JsonValue", value.value)
        case WireRecord():
            return value.to_json()
        case list() as items:
            return [_wire_value(item) for item in items]
        case dict() as mapping:
            return {key: _wire_value(item) for key, item in mapping.items()}
        case _:
            return cast("JsonValue", value)


@dataclass(frozen=True)
class WireRecord:
    """A record whose JSON is its dataclass fields in field order, under the field-name map and the omission rule."""

    def to_json(self) -> JsonObject:
        return {
            WIRE_FIELD_NAMES.get(field.name, field.name): _wire_value(getattr(self, field.name))
            for field in fields(self)
            if not (is_omitted_when_none(field) and getattr(self, field.name) is None)
        }


@dataclass(frozen=True)
class WireFrame(WireRecord):
    """A frame sent to a seat: the signal type of its class first, then the record's fields."""

    TYPE: ClassVar[StrEnum]

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, **super().to_json()}


@dataclass(frozen=True)
class ErrorFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ERROR

    reason: ErrorReason
    message: str
    capacity: int | None = None
    limit: int | None = None


def error_frame(error: HopInError) -> JsonObject:
    return ErrorFrame(reason=error.reason, message=str(error), **error.frame_fields()).to_json()


@dataclass(frozen=True)
class RoomClosedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ROOM_CLOSED

    reason: RoomClosedReason
    host_name: str
    grace_seconds: int | None = None


@dataclass(frozen=True)
class RoomCreatedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ROOM_CREATED

    room_id: str
    is_host: bool
    participant_id: PeerId
    resume_token: str


@dataclass(frozen=True)
class ExistingParticipantsFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.EXISTING_PARTICIPANTS

    participants: list[str]
    names: dict[str, str]
    is_host: bool
    locked: bool
    participant_id: PeerId
    resume_token: str


@dataclass(frozen=True)
class ParticipantJoinedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.PARTICIPANT_JOINED

    participant_id: PeerId
    name: str


@dataclass(frozen=True)
class ParticipantLeftFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.PARTICIPANT_LEFT

    participant_id: PeerId


@dataclass(frozen=True)
class HostBackFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.HOST_BACK

    participant_id: PeerId


@dataclass(frozen=True)
class HostAwayFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.HOST_AWAY

    seconds_left: int
    participant_id: PeerId


@dataclass(frozen=True)
class RoomLockedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ROOM_LOCKED


@dataclass(frozen=True)
class RoomUnlockedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ROOM_UNLOCKED


@dataclass(frozen=True)
class MediaStateBroadcastFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.MEDIA_STATE

    from_: PeerId
    audio: bool
    video: bool


@dataclass(frozen=True)
class OfferRelayFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.OFFER

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId


@dataclass(frozen=True)
class AnswerRelayFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ANSWER

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId


@dataclass(frozen=True)
class IceCandidateRelayFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.ICE_CANDIDATE

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId


RELAY_BROADCAST_FRAMES: dict[
    SignalType, type[OfferRelayFrame] | type[AnswerRelayFrame] | type[IceCandidateRelayFrame]
] = {frame_class.TYPE: frame_class for frame_class in (OfferRelayFrame, AnswerRelayFrame, IceCandidateRelayFrame)}


@dataclass(frozen=True)
class ResumedFrame(WireFrame):
    TYPE: ClassVar[SignalType] = SignalType.RESUMED

    resume_token: str
    last_seq: int


@dataclass(frozen=True)
class JoinFrame:
    room_id: str | None


@dataclass(frozen=True)
class RelayFrame:
    kind: SignalType
    target: str | None
    sdp: JsonValue
    candidate: JsonValue


@dataclass(frozen=True)
class LockFrame:
    pass


@dataclass(frozen=True)
class UnlockFrame:
    pass


@dataclass(frozen=True)
class EndRoomFrame:
    pass


@dataclass(frozen=True)
class ForeignFrame:
    """A frame of a type the call core does not know, whole as it arrived; the call's feature may handle it."""

    signal_type: str
    data: JsonObject


@dataclass(frozen=True)
class MediaStateFrame:
    audio: bool
    video: bool


@dataclass(frozen=True)
class ResumeClaim:
    participant_id: PeerId
    token: str
    last_seq: int
    sent_floor: int


@dataclass(frozen=True)
class ResumeFrame:
    """A resume frame; `claim` is None when its fields are not a well-formed claim."""

    claim: ResumeClaim | None

    def require_claim(self) -> ResumeClaim:
        if self.claim is None:
            raise ResumeRefusedError(ResumeRefusalCause.MALFORMED)
        return self.claim


type DispatchedFrame = JoinFrame | RelayFrame | LockFrame | UnlockFrame | EndRoomFrame | MediaStateFrame | ForeignFrame
type ClientFrame = DispatchedFrame | ResumeFrame


@dataclass(frozen=True)
class IncomingFrame:
    """A decoded frame: its sequence number (None when absent or invalid) and the record, or the refusal for it."""

    sequence: int | None
    frame: ClientFrame | InvalidMessageError


def _decode_join(data: JsonObject, _kind: SignalType) -> JoinFrame:
    room_id = data.get("room_id")
    if room_id is not None and not isinstance(room_id, str):
        raise InvalidMessageError("room_id must be a string")
    return JoinFrame(room_id)


def _decode_relay(data: JsonObject, kind: SignalType) -> RelayFrame:
    target = data.get("target")
    if target is not None and not isinstance(target, str):
        raise InvalidMessageError("target must be a string")
    return RelayFrame(kind, target, data.get("sdp"), data.get("candidate"))


def _decode_media_state(data: JsonObject, _kind: SignalType) -> MediaStateFrame:
    audio, video = data.get("audio"), data.get("video")
    if not isinstance(audio, bool) or not isinstance(video, bool):
        raise InvalidMessageError("media_state needs boolean audio and video")
    return MediaStateFrame(audio, video)


def _decode_resume(data: JsonObject, _kind: SignalType) -> ResumeFrame:
    participant_id = data.get("participant_id")
    token = data.get("token")
    last_seq = data.get("last_seq")
    sent_floor = data.get("sent_floor")
    if not (isinstance(participant_id, str) and isinstance(token, str)):
        return ResumeFrame(None)
    if not (_is_sequence_number(last_seq) and _is_sequence_number(sent_floor)):
        return ResumeFrame(None)
    return ResumeFrame(ResumeClaim(PeerId(participant_id), token, last_seq, sent_floor))


_DECODERS: dict[str, Callable[[JsonObject, SignalType], ClientFrame]] = {
    SignalType.JOIN.value: _decode_join,
    SignalType.OFFER.value: _decode_relay,
    SignalType.ANSWER.value: _decode_relay,
    SignalType.ICE_CANDIDATE.value: _decode_relay,
    SignalType.LOCK_ROOM.value: lambda _data, _kind: LockFrame(),
    SignalType.UNLOCK_ROOM.value: lambda _data, _kind: UnlockFrame(),
    SignalType.MEDIA_STATE.value: _decode_media_state,
    SignalType.END_ROOM.value: lambda _data, _kind: EndRoomFrame(),
    SignalType.RESUME.value: _decode_resume,
}


def _decode_payload(signal_type: str, data: JsonObject) -> ClientFrame | InvalidMessageError:
    decoder = _DECODERS.get(signal_type)
    if decoder is None:
        return ForeignFrame(signal_type, data)
    try:
        return decoder(data, SignalType(signal_type))
    except InvalidMessageError as refusal:
        return refusal


def decode_frame(raw_data: str) -> IncomingFrame:
    """Decode one client frame; raises InvalidMessageError for a frame that is no object with a signal type."""
    if len(raw_data) > MAX_SIGNALING_FRAME_LENGTH:
        raise InvalidMessageError("Message too long")
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        raise InvalidMessageError("Invalid JSON") from None
    if not isinstance(data, dict):
        raise InvalidMessageError("Message must be a JSON object")
    signal_type = data.get(TYPE_KEY)
    if not isinstance(signal_type, str):
        raise InvalidMessageError("Missing signal type")
    sequence = data.get(SEQUENCE_KEY)
    return IncomingFrame(
        sequence if _is_sequence_number(sequence) else None,
        _decode_payload(signal_type, data),
    )
