import io
import zipfile
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from webauth.config import web_auth_config
from webauth.middleware import admitted_user

from hopin.config.constants import EXPORT_APP_NAME, PEOPLE_EXPORT_VERSION, PERSON_EXPORT_PATH
from hopin.web.routes import READ_METHODS

if TYPE_CHECKING:
    from hopin.auth.stores import InMemorySessions, PersonRecords
    from hopin.signaling.handler import SeatSnapshot, SignalingHandler

EXPORT_MEMBER_NAME = "export.json"
EXPORT_DISPOSITION = 'attachment; filename="hopin-person-export.zip"'
ARCHIVE_MEDIA_TYPE = "application/zip"


# The names and values label withheld kinds in the export; none is a credential.
class WithheldReason(StrEnum):
    SECRET = "secret"  # noqa: S105
    OTHERS = "others"


class WithheldKind(StrEnum):
    SESSION_ID = "session_id"
    REFRESH_TOKEN = "refresh_token"  # noqa: S105
    RESUME_TOKEN = "resume_token"  # noqa: S105
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
    files: list[str]
    withheld: list[WithheldEntry]


def build_export(
    subject: str, exported_at: datetime, records: PersonRecords, seats: tuple[SeatSnapshot, ...]
) -> PersonExport:
    account = records.account
    held = {
        WithheldKind.SESSION_ID: (len(records.sessions), WithheldReason.SECRET),
        WithheldKind.REFRESH_TOKEN: (
            sum(session.holds_refresh_token for session in records.sessions),
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
        withheld=[WithheldEntry(kind=kind, count=count, reason=reason) for kind, (count, reason) in held.items()],
    )


def zip_of(export: PersonExport) -> bytes:
    buffer = io.BytesIO()
    member = zipfile.ZipInfo(EXPORT_MEMBER_NAME, date_time=export.exported_at.timetuple()[:6])
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, export.model_dump_json())
    return buffer.getvalue()


def create_people_router(sessions: InMemorySessions, signaling_handler: SignalingHandler) -> APIRouter:
    router = APIRouter()

    @router.api_route(PERSON_EXPORT_PATH, methods=READ_METHODS)
    async def export_person(request: Request, subject: str) -> Response:
        requester = admitted_user(request)
        config = web_auth_config(request)
        if requester is None or requester.role != config.admin_role:
            raise HTTPException(403)
        # The session store takes a threading lock, so it is read in a worker; the handler's seats are
        # read here on the event loop, where no signaling coroutine can change them meanwhile.
        records = await run_in_threadpool(sessions.person_records, subject)
        seats = signaling_handler.seats_of(subject)
        exported_at = datetime.fromtimestamp(int(config.wall_clock()), UTC)
        return Response(
            zip_of(build_export(subject, exported_at, records, seats)),
            media_type=ARCHIVE_MEDIA_TYPE,
            headers={"content-disposition": EXPORT_DISPOSITION},
        )

    return router
