from contextlib import nullcontext
from typing import TYPE_CHECKING

from webauth.middleware import SessionGate
from webauth.policies import PathRules, PublicRule

from hopin.config.constants import HEALTH_PATH, LOGIN_PATH, NO_ACCESS_PATH, SIGN_IN_PATHS, STATIC_PATH

if TYPE_CHECKING:
    from webauth.ports import SessionRecordStore

PUBLIC_PATHS = frozenset(
    {
        HEALTH_PATH,
        LOGIN_PATH,
        NO_ACCESS_PATH,
        f"{STATIC_PATH}/css/ui-console.css",
        f"{STATIC_PATH}/css/hopin.css",
        f"{STATIC_PATH}/img/hopin.svg",
    }
)


def session_gate(sessions: SessionRecordStore) -> SessionGate:
    # Each call takes the stores' shared guard, so a sign-in entry in progress holds this call back.
    return SessionGate(
        session_transaction=lambda: nullcontext(sessions),
        login_redirect=SIGN_IN_PATHS.login_redirect,
        public=(PublicRule(PathRules(exact=PUBLIC_PATHS)), *SIGN_IN_PATHS.public_rules),
    )
