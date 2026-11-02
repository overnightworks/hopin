import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, NewType, TypeGuard

from hopin.config.constants import MAX_SIGNALING_FRAME_LENGTH, ResumeRefusalCause, RoomClosedReason, SignalType
from hopin.errors import HopInError, InvalidMessageError, ResumeRefusedError

if TYPE_CHECKING:
    from collections.abc import Callable

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]

SEQUENCE_KEY = "seq"
SENDER_KEY = "from"
TYPE_KEY = "type"

PeerId = NewType("PeerId", str)


def error_frame(error: HopInError) -> JsonObject:
    return {
        TYPE_KEY: SignalType.ERROR.value,
        "reason": error.reason.value,
        "message": str(error),
        **error.frame_fields(),
    }


def _is_sequence_number(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class RoomClosedFrame:
    TYPE: ClassVar[SignalType] = SignalType.ROOM_CLOSED

    reason: RoomClosedReason
    grace_seconds: int | None = None

    def to_json(self) -> JsonObject:
        frame: JsonObject = {TYPE_KEY: self.TYPE.value, "reason": self.reason.value}
        if self.grace_seconds is not None:
            frame["grace_seconds"] = self.grace_seconds
        return frame


@dataclass(frozen=True)
class RoomCreatedFrame:
    TYPE: ClassVar[SignalType] = SignalType.ROOM_CREATED

    room_id: str
    is_host: bool
    participant_id: PeerId
    resume_token: str

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "room_id": self.room_id,
            "is_host": self.is_host,
            "participant_id": self.participant_id,
            "resume_token": self.resume_token,
        }


@dataclass(frozen=True)
class ExistingParticipantsFrame:
    TYPE: ClassVar[SignalType] = SignalType.EXISTING_PARTICIPANTS

    participants: list[str]
    names: dict[str, str]
    is_host: bool
    locked: bool
    participant_id: PeerId
    resume_token: str

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            # Copies: the frame goes into the seat's replay log, and list[str] and dict[str, str] are not JSON values.
            "participants": [*self.participants],
            "names": {**self.names},
            "is_host": self.is_host,
            "locked": self.locked,
            "participant_id": self.participant_id,
            "resume_token": self.resume_token,
        }


@dataclass(frozen=True)
class ParticipantJoinedFrame:
    TYPE: ClassVar[SignalType] = SignalType.PARTICIPANT_JOINED

    participant_id: PeerId
    name: str

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "participant_id": self.participant_id, "name": self.name}


@dataclass(frozen=True)
class ParticipantLeftFrame:
    TYPE: ClassVar[SignalType] = SignalType.PARTICIPANT_LEFT

    participant_id: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "participant_id": self.participant_id}


@dataclass(frozen=True)
class HostBackFrame:
    TYPE: ClassVar[SignalType] = SignalType.HOST_BACK

    participant_id: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "participant_id": self.participant_id}


@dataclass(frozen=True)
class HostAwayFrame:
    TYPE: ClassVar[SignalType] = SignalType.HOST_AWAY

    seconds_left: int
    participant_id: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "seconds_left": self.seconds_left, "participant_id": self.participant_id}


@dataclass(frozen=True)
class RoomLockedFrame:
    TYPE: ClassVar[SignalType] = SignalType.ROOM_LOCKED

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value}


@dataclass(frozen=True)
class RoomUnlockedFrame:
    TYPE: ClassVar[SignalType] = SignalType.ROOM_UNLOCKED

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value}


@dataclass(frozen=True)
class ChatBroadcastFrame:
    TYPE: ClassVar[SignalType] = SignalType.CHAT

    message: str
    from_: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "message": self.message, SENDER_KEY: self.from_}


@dataclass(frozen=True)
class MediaStateBroadcastFrame:
    TYPE: ClassVar[SignalType] = SignalType.MEDIA_STATE

    from_: PeerId
    audio: bool
    video: bool

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, SENDER_KEY: self.from_, "audio": self.audio, "video": self.video}


@dataclass(frozen=True)
class OfferRelayFrame:
    TYPE: ClassVar[SignalType] = SignalType.OFFER

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "sdp": self.sdp, "candidate": self.candidate, SENDER_KEY: self.from_}


@dataclass(frozen=True)
class AnswerRelayFrame:
    TYPE: ClassVar[SignalType] = SignalType.ANSWER

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "sdp": self.sdp, "candidate": self.candidate, SENDER_KEY: self.from_}


@dataclass(frozen=True)
class IceCandidateRelayFrame:
    TYPE: ClassVar[SignalType] = SignalType.ICE_CANDIDATE

    sdp: JsonValue
    candidate: JsonValue
    from_: PeerId

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "sdp": self.sdp, "candidate": self.candidate, SENDER_KEY: self.from_}


RELAY_BROADCAST_FRAMES: dict[
    SignalType, type[OfferRelayFrame] | type[AnswerRelayFrame] | type[IceCandidateRelayFrame]
] = {frame_class.TYPE: frame_class for frame_class in (OfferRelayFrame, AnswerRelayFrame, IceCandidateRelayFrame)}


@dataclass(frozen=True)
class ResumedFrame:
    TYPE: ClassVar[SignalType] = SignalType.RESUMED

    resume_token: str
    last_seq: int

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "resume_token": self.resume_token, "last_seq": self.last_seq}


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
class ChatFrame:
    message: str


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


type DispatchedFrame = (
    JoinFrame | RelayFrame | LockFrame | UnlockFrame | EndRoomFrame | ChatFrame | MediaStateFrame | ForeignFrame
)
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


def _decode_chat(data: JsonObject, _kind: SignalType) -> ChatFrame:
    message = data.get("message", "")
    if not isinstance(message, str):
        raise InvalidMessageError("message must be a string")
    return ChatFrame(message)


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
    SignalType.CHAT.value: _decode_chat,
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
