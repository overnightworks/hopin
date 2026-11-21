"""The stack a probe test runs against: HopIn with participation, and an issuer whose sign-in is a form like Keycloak's
login page, served over https beside it. The form's accounts answer a right password, a wrong one, a lockout and the
terms page."""

import html
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl

import uvicorn
from speech_client.wire import VoiceId
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from hopin.agent.speech_settings import SpeechService
from hopin.config.constants import LOGIN_GROUP
from hopin_probe.page import PASSWORD_FIELD_ID, SIGN_IN_BUTTON_ID, SIGN_IN_ERROR_ID, USERNAME_FIELD_ID
from tests.e2e.support.dialed_agent import OWNER_SUBJECT, OWNER_USERNAME
from tests.e2e.support.server import (
    LOOPBACK_HOST,
    bound_loopback_socket,
    loopback_sign_in,
    port_of,
    running_hopin,
    serving,
    write_self_signed_certificate,
)
from tests.support.agent_adders import ADDERS_GROUP
from tests.support.speech_origin import AUDIENCE_SCOPE

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    import pytest
    from starlette.requests import Request

    from tests.e2e.support.server import RunningServer
    from tests.support.fake_issuer import FakeIssuer

REALM_PATH = "/realms/probe"
SIGN_IN_PATH = f"{REALM_PATH}/protocol/openid-connect/auth"
STRANGER_USERNAME = "stranger"
LOCKED_USERNAME = "locked"
TERMS_USERNAME = "terms"

FORM = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sign in</title></head><body>
<form method="post" action="">
<input id="{USERNAME_FIELD_ID}" name="username" autocomplete="off">
<input id="{PASSWORD_FIELD_ID}" name="password" type="password">
<input id="{SIGN_IN_BUTTON_ID}" type="submit" value="Sign In">
__ERROR__
</form></body></html>"""
REFUSAL = f'<span id="{SIGN_IN_ERROR_ID}">Invalid username or password.</span>'
TERMS = """<!doctype html>
<html lang="en"><body><form method="post"><input id="kc-accept" type="submit" value="Accept"></form></body></html>"""


@dataclass(frozen=True)
class FormAccount:
    subject: str
    groups: tuple[str, ...]
    locked: bool = False
    terms_required: bool = False


ACCOUNTS = {
    OWNER_USERNAME: FormAccount(OWNER_SUBJECT, (LOGIN_GROUP, ADDERS_GROUP)),
    STRANGER_USERNAME: FormAccount("subject-stranger", (LOGIN_GROUP,)),
    LOCKED_USERNAME: FormAccount("subject-locked", (LOGIN_GROUP,), locked=True),
    TERMS_USERNAME: FormAccount("subject-terms", (LOGIN_GROUP,), terms_required=True),
}


def form_issuer_app(issuer: FakeIssuer, password: str, attempts: list[str]) -> Starlette:
    """The sign-in form of `issuer`: it signs a person of `ACCOUNTS` in with `password`, and a locked one never; each
    username it was sent is added to `attempts`."""

    async def show_form(_request: Request) -> Response:
        return HTMLResponse(FORM.replace("__ERROR__", ""))

    async def submit_form(request: Request) -> Response:
        form = dict(parse_qsl((await request.body()).decode()))
        username = form.get("username", "")
        attempts.append(username)
        account = ACCOUNTS.get(username)
        if account is None or account.locked or form.get("password") != password:
            return HTMLResponse(FORM.replace("__ERROR__", REFUSAL))
        if account.terms_required:
            return HTMLResponse(TERMS)
        query = request.url.query
        redirect_uri = dict(parse_qsl(query))["redirect_uri"]
        issuer.grant_groups(account.subject, account.groups)
        callback_query = issuer.authorize(
            f"?{query}", subject=account.subject, groups=list(account.groups), username=html.unescape(username)
        )
        return RedirectResponse(f"{redirect_uri}?{callback_query}", status_code=302)

    return Starlette(
        routes=[Route(SIGN_IN_PATH, show_form), Route(SIGN_IN_PATH, submit_form, methods=["POST"])],
    )


def https_server(app: Starlette, certificate: tuple[Path, Path]) -> uvicorn.Server:
    key_file, certificate_file = certificate
    return uvicorn.Server(
        uvicorn.Config(app, ssl_keyfile=str(key_file), ssl_certfile=str(certificate_file), log_level="warning")
    )


@dataclass(frozen=True)
class ProbeStack:
    server: RunningServer
    password: str
    attempts: list[str]


@contextmanager
def probe_stack(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    *,
    speech_origin: str | None = None,
    hears: bool = False,
) -> Iterator[ProbeStack]:
    """HopIn with participation whose adders group holds `OWNER_SUBJECT`'s agents, signing in through the form issuer;
    its agents speak through the speech service at `speech_origin` when there is one, and hear when `hears`."""
    issuer_listener = bound_loopback_socket()
    issuer_url = f"https://{LOOPBACK_HOST}:{port_of(issuer_listener)}{REALM_PATH}"
    loopback = loopback_sign_in(tmp_path_factory.mktemp(f"{name}-sign-in"), issuer=issuer_url)
    certificate = write_self_signed_certificate(tmp_path_factory.mktemp(f"{name}-tls"))
    speech = SpeechService(speech_origin or "http://speech.invalid:8090", VoiceId.THORSTEN, AUDIENCE_SCOPE)
    password = secrets.token_urlsafe(12)
    attempts: list[str] = []
    with (
        running_hopin(
            loopback,
            tmp_path_factory.mktemp(f"{name}-hopin-tls"),
            participation=True,
            hears=hears,
            speaks=speech_origin is not None,
            speech=speech,
            agent_adders={OWNER_SUBJECT: ADDERS_GROUP},
            certificate=certificate,
        ) as server,
        serving(https_server(form_issuer_app(server.issuer, password, attempts), certificate), issuer_listener),
    ):
        yield ProbeStack(server, password, attempts)
