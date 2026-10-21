import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import Morsel, SimpleCookie

from starlette.responses import Response
from webauth.config import WebAuthConfig
from webauth.cookies import issue_session_cookies

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
    lifetime = expires_in or timedelta(seconds=config.session_lifetime.absolute_max_age_seconds)
    session = stores.sessions.create(account.id, now=now, expires_at=now + lifetime)
    response = Response()
    issue_session_cookies(response, session.id, config)
    return SeededMember(
        account=account, session=session, set_cookie_headers=tuple(response.headers.getlist("set-cookie"))
    )
