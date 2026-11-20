"""The frames skat sends a seat, each a record the browser's wire is generated from."""

from dataclasses import dataclass
from typing import ClassVar, get_args

from hopin.signaling.frames import WireFrame
from hopin.skat.vocabulary import SkatRefusal, SkatSignal


@dataclass(frozen=True)
class AttachedTableFrame(WireFrame):
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_TABLE

    revision: int
    attachment: int
    table: str
    src: str
    opener_id: str | None


@dataclass(frozen=True)
class EmptyTableFrame(WireFrame):
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_TABLE

    revision: int
    attachment: None
    table: None
    src: None
    opener_id: None


@dataclass(frozen=True)
class RefusedFrame(WireFrame):
    TYPE: ClassVar[SkatSignal] = SkatSignal.SKAT_REFUSED

    reason: SkatRefusal


type SkatFrame = AttachedTableFrame | EmptyTableFrame | RefusedFrame

# The frames the generator renders into skat's own browser wire, in the order they appear there.
BROWSER_FRAMES = get_args(SkatFrame.__value__)
