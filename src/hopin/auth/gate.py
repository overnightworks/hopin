from contextlib import nullcontext
from typing import TYPE_CHECKING

from webauth.dependencies import LoginRedirect
from webauth.middleware import SessionGate
from webauth.policies import PathRules, PublicRule

from hopin.config.constants import (
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    HEALTH_PATH,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    RETURN_TO_QUERY_PARAMETER,
    STATIC_PATH,
)

if TYPE_CHECKING:
    from webauth.ports import SessionRecordStore

PUBLIC_PATHS = frozenset(
    {
        HEALTH_PATH,
        AUTH_START_PATH,
        AUTH_CALLBACK_PATH,
        LOGIN_PATH,
        NO_ACCESS_PATH,
        LOGOUT_PATH,
        f"{STATIC_PATH}/css/ui-console.css",
        f"{STATIC_PATH}/css/hopin.css",
    }
)
LOGIN_REDIRECT = LoginRedirect(path=AUTH_START_PATH, redirect_query_param=RETURN_TO_QUERY_PARAMETER)


def session_gate(sessions: SessionRecordStore) -> SessionGate:
    # InMemorySessions applies each call at once, so no transaction is needed around it.
    return SessionGate(
        session_transaction=lambda: nullcontext(sessions),
        login_redirect=LOGIN_REDIRECT,
        public=(
            PublicRule(PathRules(exact=PUBLIC_PATHS)),
            PublicRule(PathRules(exact=frozenset({BACKCHANNEL_LOGOUT_PATH})), methods=frozenset({"POST"})),
        ),
    )
