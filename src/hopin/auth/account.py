from fastapi import APIRouter, Request
from pydantic import BaseModel
from webauth.middleware import admitted_user

from hopin.config.constants import ACCOUNT_PATH
from hopin.web.routes import READ_METHODS


class AccountName(BaseModel):
    name: str


def create_account_router() -> APIRouter:
    router = APIRouter()

    @router.api_route(ACCOUNT_PATH, methods=READ_METHODS)
    def account(request: Request) -> AccountName:
        user = admitted_user(request)
        # The gate refuses a request without a session before the route runs, so a missing user is a wiring fault.
        if user is None:  # pragma: no cover
            message = "The account route ran without an admitted user."
            raise RuntimeError(message)
        return AccountName(name=user.username)

    return router
