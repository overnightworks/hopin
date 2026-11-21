"""The session gate in front of every route and `/ws`, and the bearer gate in front of a feature's protected resource.

Without a session only these pass: `PUBLIC_PATHS`; the stylesheets and the icon the built sign-in pages link under
`/_app/` and the fonts those stylesheets reference (`sign_in_assets`, read from the page build); and the sign-in
routes' own public rules, which webauth derives from `SIGN_IN_PATHS` (start, callback, and the POSTs of logout and
Keycloak's back-channel logout). webauth adds the protected resource's own public rule when a feature serves one.
"""

from webauth.middleware import BearerGate, ProtectedResource, SessionGate
from webauth.policies import PathRules, PublicRule

from hopin.config.constants import (
    HEALTH_PATH,
    LOGIN_PATH,
    NO_ACCESS_PATH,
)
from hopin.config.web_policy import SIGN_IN_PATHS

PUBLIC_PATHS = frozenset(
    {
        HEALTH_PATH,
        LOGIN_PATH,
        NO_ACCESS_PATH,
    }
)


def session_gate(sign_in_assets: frozenset[str]) -> SessionGate:
    return SessionGate(
        login_redirect=SIGN_IN_PATHS.login_redirect,
        public=(PublicRule(PathRules(exact=PUBLIC_PATHS | sign_in_assets)),),
    )


def bearer_gate(resource: ProtectedResource) -> BearerGate:
    """The one door a bearer token opens: the resource's entry point, for a person the issuer lets in."""
    return BearerGate(resource=resource)
