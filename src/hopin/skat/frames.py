"""The frames skat sends a seat, each a record the browser's wire is generated from."""

from dataclasses import dataclass
from typing import ClassVar, get_args

from hopin.signaling.frames import TYPE_KEY, JsonObject
from hopin.skat.vocabulary import SkatRefusal, SkatSignal


@dataclass(frozen=True)
class AttachedTableFrame:
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_TABLE

    revision: int
    attachment: int
    table: str
    src: str
    opener_id: str | None

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "revision": self.revision,
            "attachment": self.attachment,
            "table": self.table,
            "src": self.src,
            "opener_id": self.opener_id,
        }


@dataclass(frozen=True)
class EmptyTableFrame:
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_TABLE

    revision: int
    attachment: None
    table: None
    src: None
    opener_id: None

    def to_json(self) -> JsonObject:
        return {
            TYPE_KEY: self.TYPE.value,
            "revision": self.revision,
            "attachment": self.attachment,
            "table": self.table,
            "src": self.src,
            "opener_id": self.opener_id,
        }


@dataclass(frozen=True)
class RefusedFrame:
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_REFUSED

    reason: SkatRefusal

    def to_json(self) -> JsonObject:
        return {TYPE_KEY: self.TYPE.value, "reason": self.reason.value}


type SkatFrame = AttachedTableFrame | EmptyTableFrame | RefusedFrame

# The frames the generator renders into skat's own browser wire, in the order they appear there.
BROWSER_FRAMES = get_args(SkatFrame.__value__)
