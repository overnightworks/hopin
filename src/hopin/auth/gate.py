"""The session gate in front of every route and `/ws`, and the bearer gate in front of a feature's protected resource.

Without a session only these pass: `PUBLIC_PATHS`; the stylesheets and the icon the built sign-in pages link under
`/_app/` and the fonts those stylesheets reference (`sign_in_assets`, read from the page build); and the sign-in
routes' own public rules, which webauth derives from `SIGN_IN_PATHS` (start, callback, and the POSTs of logout and
Keycloak's back-channel logout). The resource's own public rule joins them when a feature serves one.
"""

from typing import TYPE_CHECKING

from webauth.admission import AdmissionConfig
from webauth.middleware import BearerGate, SessionGate
from webauth.policies import PathRules, PublicRule

from hopin.config.constants import (
    ADMIN_GROUP,
    HEALTH_PATH,
    LOGIN_GROUP,
    LOGIN_PATH,
    NO_ACCESS_PATH,
)
from hopin.config.web_policy import SIGN_IN_PATHS
from hopin.stores.session_transaction import session_transaction_of

if TYPE_CHECKING:
    from webauth.middleware import ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

PUBLIC_PATHS = frozenset(
    {
        HEALTH_PATH,
        LOGIN_PATH,
        NO_ACCESS_PATH,
    }
)


def session_gate(
    sessions: SessionRecordStore, sign_in_assets: frozenset[str], resource: ProtectedResource | None = None
) -> SessionGate:
    # Each call takes the stores' shared guard, so a sign-in entry in progress holds this call back.
    return SessionGate(
        session_transaction=session_transaction_of(sessions),
        login_redirect=SIGN_IN_PATHS.login_redirect,
        public=(
            PublicRule(PathRules(exact=PUBLIC_PATHS | sign_in_assets)),
            *(() if resource is None else (resource.public_rule,)),
        ),
    )


def bearer_gate(relying_party: OidcRelyingParty, resource: ProtectedResource) -> BearerGate:
    """The one door a bearer token opens: the resource's entry point, for a person the issuer lets in."""
    admission = AdmissionConfig(bound_issuer=relying_party.issuer, login_group=LOGIN_GROUP, admin_group=ADMIN_GROUP)
    return BearerGate(relying_party=relying_party, admission=admission, resource=resource)
