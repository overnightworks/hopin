import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

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


def seed_member(stores: SignInStores, config: WebAuthConfig) -> SeededMember:
    """Create a fresh account and a session inside the configured lifetime, and the cookies webauth issues for it."""
    suffix = secrets.token_hex(SUBJECT_BYTES)
    account = stores.accounts.create_for_subject(f"subject-{suffix}", f"member-{suffix}", role=config.user_role)
    now = datetime.fromtimestamp(int(config.wall_clock()), UTC)
    lifetime = timedelta(seconds=config.session_lifetime.absolute_max_age_seconds)
    session = stores.sessions.create(account.id, now=now, expires_at=now + lifetime)
    response = Response()
    issue_session_cookies(response, session.id, config)
    return SeededMember(
        account=account, session=session, set_cookie_headers=tuple(response.headers.getlist("set-cookie"))
    )
