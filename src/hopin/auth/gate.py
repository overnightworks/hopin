from contextlib import nullcontext
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
    STATIC_PATH,
)
from hopin.config.web_policy import SIGN_IN_PATHS

if TYPE_CHECKING:
    from webauth.middleware import ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore

PUBLIC_PATHS = frozenset(
    {
        HEALTH_PATH,
        LOGIN_PATH,
        NO_ACCESS_PATH,
        f"{STATIC_PATH}/css/hopin.css",
        f"{STATIC_PATH}/img/hopin.svg",
    }
)


def session_gate(
    sessions: SessionRecordStore, sign_in_assets: frozenset[str], resource: ProtectedResource | None = None
) -> SessionGate:
    # Each call takes the stores' shared guard, so a sign-in entry in progress holds this call back.
    return SessionGate(
        session_transaction=lambda: nullcontext(sessions),
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
