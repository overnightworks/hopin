import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from webauth.single_process_stores import SingleProcessStores

from hopin.auth.sign_in import LoggingAuditSink
from hopin.participation.voice import StoredVoice, VoiceStore

if TYPE_CHECKING:
    from webauth.ports import AuditSink, SessionRecord, SessionRecordStore


def single_process_stores(audit: AuditSink | None = None) -> SingleProcessStores:
    """The stores of production's wiring; a test that needs another audit sink passes it."""
    return SingleProcessStores(audit=LoggingAuditSink() if audit is None else audit)


@dataclass(frozen=True)
class SessionHold:
    """Parks the loads of one session: `parked` is set when one waits, `release` lets it go on."""

    parked: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)


class WatchedSessions:
    """The session store, noting every load by session id and, for a session it is told to, holding or failing the
    load; everything else goes to the real store."""

    def __init__(self, sessions: SessionRecordStore) -> None:
        self._sessions = sessions
        self.loaded: list[str] = []
        self._failing: set[str] = set()
        self._holds: dict[str, SessionHold] = {}

    def fail_loads_of(self, session_id: str) -> None:
        self._failing.add(session_id)

    def hold_loads_of(self, session_id: str) -> SessionHold:
        hold = SessionHold()
        self._holds[session_id] = hold
        return hold

    def load(self, session_id: str) -> SessionRecord | None:
        self.loaded.append(session_id)
        if session_id in self._failing:
            raise ConnectionError
        if (hold := self._holds.get(session_id)) is not None:
            hold.parked.set()
            hold.release.wait(timeout=10)
        return self._sessions.load(session_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._sessions, name)


class WatchedVoices(VoiceStore):
    """The voice store, counting the sounds put into it: the speaking path puts a sentence's sound just before it queues
    the sentence, in the same step of the app's loop, so a step run on the loop after the count rose finds it queued."""

    def __init__(self) -> None:
        super().__init__()
        self._count = threading.Condition()
        self.stored = 0

    def put(self, room_id: str, speaker: str, wav: bytes) -> StoredVoice:
        stored = super().put(room_id, speaker, wav)
        with self._count:
            self.stored += 1
            self._count.notify_all()
        return stored

    def wait_for(self, stored: int, timeout_seconds: float) -> None:
        with self._count:
            if not self._count.wait_for(lambda: self.stored >= stored, timeout_seconds):
                raise TimeoutError(f"{stored} sounds were not stored in {timeout_seconds} seconds")
