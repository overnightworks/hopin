"""The identity provider of the joint drive: an HTTPS issuer that exists only in the drive, signing with a key it
generated at run time, for Skat's client alone. `/auth` shows a page and redirects only after a click, so a person can
close the sign-in window before it finishes; it offers a member of `/apps/skat` and a person who holds no group.

    python -m tests.e2e.support.drive_issuer --port N --certificate F --key F --issuer URL --client-id ID \
        --client-secret-file F
"""

import argparse
import html
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit

import uvicorn
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route
from webauth.oidc import TokenRequest

from tests.support.fake_issuer import FakeIssuer

if TYPE_CHECKING:
    from starlette.requests import Request

ACCOUNTS = {
    "member": ("drive-member", ["/apps/skat"], "Ada Member"),
    "outsider": ("drive-outsider", [], "Otto Outsider"),
}
APPROVE_PATH = "/auth/approve"
ACCOUNT_FIELD = "account"
REDIRECT_HOST_SUFFIX = ".drive.localhost"
OPENID_CONNECT = "/protocol/openid-connect"

APPROVAL_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Drive sign-in</title></head><body>
<h1>Sign in to the drive</h1>
<p>This is the drive's own issuer. Choose who signs in.</p>
__FORMS__
</body></html>"""


def _approval_form(approve_url: str, parameters: list[tuple[str, str]], account: str, label: str) -> str:
    hidden = "".join(
        f'<input type="hidden" name="{html.escape(name)}" value="{html.escape(value)}">' for name, value in parameters
    )
    return (
        f'<form method="get" action="{html.escape(approve_url)}">{hidden}'
        f'<input type="hidden" name="{ACCOUNT_FIELD}" value="{account}">'
        f'<button type="submit" id="approve-{account}">{html.escape(label)}</button></form>'
    )


def drive_issuer_app(issuer_url: str, client_id: str, client_secret: str) -> Starlette:
    issuer = FakeIssuer(
        SimpleNamespace(  # type: ignore[arg-type]
            settings=SimpleNamespace(oidc_issuer=issuer_url, oidc_client_id=client_id), client_secret=client_secret
        )
    )
    realm_path = urlsplit(issuer_url).path

    async def authorize(request: Request) -> Response:
        parameters = parse_qsl(request.url.query)
        forms = "".join(
            _approval_form(f"{realm_path}{OPENID_CONNECT}{APPROVE_PATH}", parameters, account, f"Approve as {name}")
            for account, (_, _, name) in ACCOUNTS.items()
        )
        return HTMLResponse(APPROVAL_PAGE.replace("__FORMS__", forms))

    async def approve(request: Request) -> Response:
        parameters = [(name, value) for name, value in parse_qsl(request.url.query) if name != ACCOUNT_FIELD]
        account = request.query_params.get(ACCOUNT_FIELD, "")
        redirect_uri = dict(parameters).get("redirect_uri", "")
        if account not in ACCOUNTS or not (urlsplit(redirect_uri).hostname or "").endswith(REDIRECT_HOST_SUFFIX):
            return Response("unknown account or redirect", status_code=400)
        subject, groups, username = ACCOUNTS[account]
        callback_query = issuer.authorize(
            f"?{urlencode(parameters)}", subject=subject, groups=groups, username=username
        )
        return RedirectResponse(f"{redirect_uri}?{callback_query}", status_code=302)

    async def token(request: Request) -> Response:
        form = dict(parse_qsl((await request.body()).decode()))
        answer = issuer.post_token_request(
            TokenRequest(str(request.url), form, SecretStr(request.headers.get("authorization", "")))
        )
        return Response(answer.body, status_code=answer.status, media_type="application/json")

    async def certificates(_request: Request) -> Response:
        return Response(issuer.fetch_key_set(""), media_type="application/json")

    async def logout(request: Request) -> Response:
        target = request.query_params.get("post_logout_redirect_uri", "")
        if (urlsplit(target).hostname or "").endswith(REDIRECT_HOST_SUFFIX):
            return RedirectResponse(target, status_code=302)
        return HTMLResponse("<p>Signed out of the drive.</p>")

    base = f"{realm_path}{OPENID_CONNECT}"
    return Starlette(
        routes=[
            Route(f"{base}/auth", authorize),
            Route(f"{base}{APPROVE_PATH}", approve),
            Route(f"{base}/token", token, methods=["POST"]),
            Route(f"{base}/certs", certificates),
            Route(f"{base}/logout", logout),
        ]
    )


def main(arguments: list[str]) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--client-secret-file", required=True)
    options = parser.parse_args(arguments)
    secret = Path(options.client_secret_file).read_text().strip()
    uvicorn.run(
        drive_issuer_app(options.issuer, options.client_id, secret),
        host="127.0.0.1",
        port=options.port,
        ssl_certfile=options.certificate,
        ssl_keyfile=options.key,
        log_level="warning",
    )


if __name__ == "__main__":
    import sys

    main(sys.argv[1:])
