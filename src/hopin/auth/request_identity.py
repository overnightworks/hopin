from typing import TYPE_CHECKING

from logline import set_client_ip, set_sub
from starlette.requests import HTTPConnection
from webauth.middleware import admitted_user
from webauth.proxies import resolve_client_ip

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestIdentityMiddleware:
    """Tells the request line who the request is from: the address webauth derives under the trusted
    proxies, and the `sub` of the person the session gate admitted.

    It sits outside the session gate so a refused request still carries its address, and reads the
    admission when the response starts, before the line is written.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        connection = HTTPConnection(scope)
        set_client_ip(scope, resolve_client_ip(connection))

        async def send_naming_sub(message: Message) -> None:
            if message["type"] == "http.response.start":
                set_sub(scope, _admitted_sub(connection))
            await send(message)

        await self._app(scope, receive, send_naming_sub)


def _admitted_sub(connection: HTTPConnection) -> str | None:
    try:
        user = admitted_user(connection)
    except RuntimeError:
        return None  # no gate decided this request (a refusal before the gate)
    return None if user is None else user.id
