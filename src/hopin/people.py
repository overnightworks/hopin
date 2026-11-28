import io
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from webauth.config import web_auth_config
from webauth.middleware import admitted_admin

from hopin.config.constants import (
    EXPORT_APP_NAME,
    PEOPLE_ERASURE_VERSION,
    PEOPLE_EXPORT_VERSION,
    PERSON_EXPORT_PATH,
    PERSON_PATH,
    READ_METHODS,
)
from hopin.stores.identity import ErasedIdentity

if TYPE_CHECKING:
    from hopin.signaling.handler import PersonReach, SignalingHandler
    from hopin.signaling.seats import SeatSnapshot
    from hopin.stores.identity import IdentityStores, PersonRecords

logger: structlog.stdlib.BoundLogger = structlog.get_logger()

EXPORT_MEMBER_NAME = "export.json"
EXPORT_DISPOSITION = 'attachment; filename="hopin-person-export.zip"'
ARCHIVE_MEDIA_TYPE = "application/zip"


# The names and values label withheld kinds in the export; none is a credential.
class WithheldReason(StrEnum):
    SECRET = "secret"  # noqa: S105 - label of a withheld kind and no credential
    OTHERS = "others"


class WithheldKind(StrEnum):
    SESSION_ID = "session_id"
    REFRESH_TOKEN = "refresh_token"  # noqa: S105 - label of a withheld kind and no credential
    ID_TOKEN = "id_token"  # noqa: S105 - label of a withheld kind and no credential
    RESUME_TOKEN = "resume_token"  # noqa: S105 - label of a withheld kind and no credential
    CALL_REPLAY_FRAME = "call_replay_frame"


class AccountEntry(BaseModel):
    name: str
    role: str


class SessionEntry(BaseModel):
    started_at: datetime
    expires_at: datetime


class SeatEntry(BaseModel):
    call_id: str | None
    host: bool


class PersonData(BaseModel):
    account: AccountEntry | None
    sessions: list[SessionEntry]
    seats: list[SeatEntry]


class FileEntry(BaseModel):
    path: str
    kind: str
    bytes: int


class WithheldEntry(BaseModel):
    kind: WithheldKind
    count: int
    reason: WithheldReason


class PersonExport(BaseModel):
    people_export: int
    app: str
    subject: str
    exported_at: datetime
    data: PersonData
    files: list[FileEntry]
    withheld: list[WithheldEntry]


def build_export(
    subject: str, exported_at: datetime, records: PersonRecords, seats: tuple[SeatSnapshot, ...]
) -> PersonExport:
    account = records.account
    holds_nothing = account is None and not records.sessions and not seats
    held = {
        WithheldKind.SESSION_ID: (len(records.sessions), WithheldReason.SECRET),
        WithheldKind.REFRESH_TOKEN: (
            sum(session.holds_refresh_token for session in records.sessions),
            WithheldReason.SECRET,
        ),
        WithheldKind.ID_TOKEN: (
            sum(session.holds_id_token for session in records.sessions),
            WithheldReason.SECRET,
        ),
        WithheldKind.RESUME_TOKEN: (len(seats), WithheldReason.SECRET),
        WithheldKind.CALL_REPLAY_FRAME: (sum(seat.replay_frames for seat in seats), WithheldReason.OTHERS),
    }
    return PersonExport(
        people_export=PEOPLE_EXPORT_VERSION,
        app=EXPORT_APP_NAME,
        subject=subject,
        exported_at=exported_at,
        data=PersonData(
            account=None if account is None else AccountEntry(name=account.username, role=account.role),
            sessions=[SessionEntry(started_at=s.started_at, expires_at=s.expires_at) for s in records.sessions],
            seats=[SeatEntry(call_id=seat.call_id, host=seat.host) for seat in seats],
        ),
        files=[],
        withheld=[]
        if holds_nothing
        else [WithheldEntry(kind=kind, count=count, reason=reason) for kind, (count, reason) in held.items()],
    )


class ErasedKind(StrEnum):
    ACCOUNT = "account"
    SESSION = "session"
    SEAT = "seat"


class AffectedKind(StrEnum):
    CALL = "call"


class AffectedAction(StrEnum):
    END = "end"


class KindCount(BaseModel):
    kind: ErasedKind
    count: int


class AffectedCount(BaseModel):
    kind: AffectedKind
    count: int
    action: AffectedAction


class PersonErasure(BaseModel):
    people_erasure: int
    app: str
    subject: str
    dry_run: bool
    kinds: list[KindCount]
    affects_others: list[AffectedCount]


def build_erasure(subject: str, *, dry_run: bool, identity: ErasedIdentity, reach: PersonReach) -> PersonErasure:
    """Every kind is listed, zero counts included, so the shape never varies."""
    return PersonErasure(
        people_erasure=PEOPLE_ERASURE_VERSION,
        app=EXPORT_APP_NAME,
        subject=subject,
        dry_run=dry_run,
        kinds=[
            KindCount(kind=ErasedKind.ACCOUNT, count=identity.accounts),
            KindCount(kind=ErasedKind.SESSION, count=identity.sessions),
            KindCount(kind=ErasedKind.SEAT, count=reach.seats),
        ],
        affects_others=[AffectedCount(kind=AffectedKind.CALL, count=reach.calls, action=AffectedAction.END)],
    )


def zip_of(export: PersonExport) -> bytes:
    buffer = io.BytesIO()
    member = zipfile.ZipInfo(EXPORT_MEMBER_NAME, date_time=export.exported_at.timetuple()[:6])
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, export.model_dump_json())
    return buffer.getvalue()


def create_people_router(identity: IdentityStores, signaling_handler: SignalingHandler) -> APIRouter:
    router = APIRouter()

    @router.api_route(PERSON_EXPORT_PATH, methods=READ_METHODS, dependencies=[Depends(admitted_admin)])
    async def export_person(request: Request, subject: str) -> Response:
        config = web_auth_config(request)
        # The store is read on a worker thread; the handler's seats are read here on the event loop.
        records = await run_in_threadpool(identity.records_of, subject)
        seats = await signaling_handler.seats_of(subject)
        exported_at = datetime.fromtimestamp(int(config.wall_clock()), UTC)
        return Response(
            zip_of(build_export(subject, exported_at, records, seats)),
            media_type=ARCHIVE_MEDIA_TYPE,
            headers={"content-disposition": EXPORT_DISPOSITION},
        )

    @router.delete(PERSON_PATH, dependencies=[Depends(admitted_admin)])
    async def erase_person(subject: str, *, dry_run: bool = False) -> PersonErasure:
        if dry_run:
            # Reads only: no lock, no wait, no change. The store is read on a worker thread, the seats on the loop.
            stored = await run_in_threadpool(identity.records_of, subject)
            held = ErasedIdentity(accounts=int(stored.account is not None), sessions=len(stored.sessions))
            reach = await signaling_handler.reach_of(subject)
        else:
            # The rows go first: a seat ended while its session lives is re-opened by the page's reconnect.
            held = await run_in_threadpool(identity.erase, subject)
            reach = await signaling_handler.end_all_of(subject)
        await logger.ainfo(
            "person.erased", dry_run=dry_run, accounts=held.accounts, sessions=held.sessions, **asdict(reach)
        )
        return build_erasure(subject, dry_run=dry_run, identity=held, reach=reach)

    return router
