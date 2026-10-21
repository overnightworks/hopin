from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from webauth.dependencies import current_user_dependency
from webauth.ports import AuthenticatedUser

from hopin.config.constants import ACCOUNT_PATH
from hopin.web.routes import READ_METHODS

if TYPE_CHECKING:
    from webauth.ports import SessionRecordStore


class AccountName(BaseModel):
    name: str


def create_account_router(sessions: SessionRecordStore) -> APIRouter:
    dependencies = current_user_dependency(
        session_store=lambda: sessions,
        # HopIn's log context binds nothing and no log names a username.
        on_authenticated=lambda _user: None,
    )
    router = APIRouter()

    @router.api_route(ACCOUNT_PATH, methods=READ_METHODS)
    def account(user: AuthenticatedUser = Depends(dependencies.current_user)) -> AccountName:  # noqa: B008
        return AccountName(name=user.username)

    return router
