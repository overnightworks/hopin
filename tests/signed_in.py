import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import Morsel, SimpleCookie
from typing import TYPE_CHECKING

from starlette.responses import Response
from webauth.cookies import issue_session_cookies

from tests.sign_in_settings import TEST_PUBLIC_ORIGIN

if TYPE_CHECKING:
    from collections.abc import Iterable

    from webauth.config import WebAuthConfig

    from hopin.auth.stores import SignInStores, StoredAccount, StoredSession

SUBJECT_BYTES = 12


@dataclass(frozen=True)
class SeededMember:
    account: StoredAccount
    session: StoredSession = field(repr=False)
    set_cookie_headers: tuple[str, ...] = field(repr=False)


def parsed_set_cookies(set_cookie_headers: Iterable[str]) -> list[tuple[str, Morsel[str]]]:
    """The one parse of Set-Cookie values: each cookie's name with its morsel."""
    return [item for header in set_cookie_headers for item in SimpleCookie(header).items()]


def cookie_header(set_cookie_headers: Iterable[str]) -> str:
    """The Cookie header a browser would send back for these Set-Cookie values."""
    return "; ".join(f"{name}={morsel.value}" for name, morsel in parsed_set_cookies(set_cookie_headers))


def handshake_headers(member: SeededMember | None, origin: str | None = TEST_PUBLIC_ORIGIN) -> dict[str, str]:
    """The headers of a socket handshake: the origin, and the member's cookies when there is one."""
    headers = {} if origin is None else {"origin": origin}
    if member is not None:
        headers["cookie"] = cookie_header(member.set_cookie_headers)
    return headers


def seed_member(
    stores: SignInStores,
    config: WebAuthConfig,
    *,
    account: StoredAccount | None = None,
    expires_in: timedelta | None = None,
) -> SeededMember:
    """Seed a session inside the configured lifetime, and the cookies webauth issues for it.

    Without `account` the member is a fresh account; with it, the session is a further device of that account.
    `expires_in` ends the session that far from now instead of at the full lifetime.
    """
    if account is None:
        suffix = secrets.token_hex(SUBJECT_BYTES)
        account = stores.accounts.create_for_subject(f"subject-{suffix}", f"member-{suffix}", role=config.user_role)
    now = datetime.fromtimestamp(int(config.wall_clock()), UTC)
    lifetime = timedelta(seconds=config.session_lifetime.absolute_max_age_seconds) if expires_in is None else expires_in
    session = stores.sessions.create(account.id, now=now, expires_at=now + lifetime, refresh_token=None)
    response = Response()
    issue_session_cookies(response, session.id, config)
    return SeededMember(
        account=account, session=session, set_cookie_headers=tuple(response.headers.getlist("set-cookie"))
    )
