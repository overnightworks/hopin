from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webauth.oidc import OidcRefusal
    from webauth.oidc_login import AccessTokenRefusal


def issuer_error(issuer_refusal: OidcRefusal | None) -> str | None:
    idp_error = None if issuer_refusal is None else issuer_refusal.idp_error
    return None if idp_error is None else idp_error.value


def refusal_reason(issuer_refusal: OidcRefusal) -> str:
    """The closed code a refused step logs: the issuer's own error code, else webauth's reason for the refusal."""
    return issuer_error(issuer_refusal) or issuer_refusal.reason.value


def grant_refusal_reason(refusal: AccessTokenRefusal) -> str:
    """The closed code a refused grant logs: the issuer's own error code when it named one, else webauth's reason."""
    return issuer_error(refusal.issuer_refusal) or refusal.reason.value
