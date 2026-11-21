import threading
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from webauth.single_process_stores import SingleProcessStores

from hopin.auth.sign_in import LoggingAuditSink
from hopin.participation.voice import StoredVoice, VoiceStore
from hopin.stores.accounts import AccountRecord
from hopin.stores.identity import PersonRecords, SessionTimes

if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from datetime import datetime

    from webauth.ports import AuditSink, SessionRecord, SessionRecordStore, SessionTransaction, SignInStores


def session_transaction_of(sessions: SessionRecordStore) -> SessionTransaction:
    """The one transaction every session read opens; the in-memory store commits each call itself."""
    return lambda: nullcontext(sessions)


class InThreadIdentity:
    """webauth's single-process stores behind `IdentityStores`, for the tests that run the app in one process.

    A test seeds `accounts` and `sessions`, and may replace `sessions` with a watching wrapper the app then reads.
    """

    def __init__(self, audit: AuditSink) -> None:
        stores = SingleProcessStores(audit=audit)
        self.accounts = stores.accounts
        self.sessions = stores.sessions
        self.transaction = stores.transaction

    def sign_in_transaction(self) -> AbstractContextManager[SignInStores]:
        return self.transaction()

    def session_transaction(self) -> AbstractContextManager[SessionRecordStore]:
        return nullcontext(self.sessions)

    def records_of(self, subject: str) -> PersonRecords:
        records = self.sessions.records_of(subject)
        account = records.account
        return PersonRecords(
            account=None
            if account is None
            else AccountRecord(id=account.id, username=account.username, role=account.role, kind=account.kind),
            sessions=tuple(
                SessionTimes(
                    started_at=session.started_at,
                    expires_at=session.expires_at,
                    holds_refresh_token=session.holds_refresh_token,
                    holds_id_token=session.holds_id_token,
                )
                for session in records.sessions
            ),
        )

    def delete_expired(self, at: datetime) -> int:
        return self.sessions.delete_expired(at)


def single_process_stores(audit: AuditSink | None = None) -> InThreadIdentity:
    """The in-thread tests' identity; a test that needs another audit sink passes it."""
    return InThreadIdentity(LoggingAuditSink() if audit is None else audit)


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
    """The voice store, counting the sounds opened in it: the speaking path opens a sentence's sound just before it
    queues the sentence, in the same step of the app's loop, so a step run on the loop after the count rose finds it
    queued. A sound is released in the step that ends its sentence, which the count of releases also tells."""

    def __init__(self) -> None:
        super().__init__()
        self._count = threading.Condition()
        self.stored = 0
        self.released = 0

    def open(self, room_id: str, speaker: str) -> StoredVoice:
        stored = super().open(room_id, speaker)
        with self._count:
            self.stored += 1
            self._count.notify_all()
        return stored

    def release(self, voice_id: str) -> None:
        super().release(voice_id)
        with self._count:
            self.released += 1
            self._count.notify_all()

    def wait_for(self, stored: int, timeout_seconds: float) -> None:
        with self._count:
            if not self._count.wait_for(lambda: self.stored >= stored, timeout_seconds):
                raise TimeoutError(f"{stored} sounds were not stored in {timeout_seconds} seconds")

    def wait_for_released(self, released: int, timeout_seconds: float) -> None:
        with self._count:
            if not self._count.wait_for(lambda: self.released >= released, timeout_seconds):
                raise TimeoutError(f"{released} sounds were not released in {timeout_seconds} seconds")


async def sound_of(voice: StoredVoice | None) -> bytes | None:
    """What a reader of the voice gets from its start to its end, or nothing for no voice."""
    return None if voice is None else b"".join([chunk async for chunk in voice.read()])
