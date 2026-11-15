from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webauth.oidc import OidcRefusal
    from webauth.oidc_login import AccessTokenRefusal


class IssuerError(StrEnum):
    """The token endpoint's error codes of RFC 6749 section 5.2; any other code the issuer names is `other`."""

    INVALID_REQUEST = "invalid_request"
    INVALID_CLIENT = "invalid_client"
    INVALID_GRANT = "invalid_grant"
    UNAUTHORIZED_CLIENT = "unauthorized_client"
    UNSUPPORTED_GRANT_TYPE = "unsupported_grant_type"
    INVALID_SCOPE = "invalid_scope"
    OTHER = "other"

    @classmethod
    def of(cls, idp_error: str) -> IssuerError:
        # Only this closed vocabulary reaches a log: the issuer's own text may be shaped like a credential.
        try:
            return cls(idp_error)
        except ValueError:
            return cls.OTHER


def issuer_error(issuer_refusal: OidcRefusal | None) -> str | None:
    idp_error = None if issuer_refusal is None else issuer_refusal.idp_error
    return None if idp_error is None else IssuerError.of(idp_error).value


def refusal_reason(issuer_refusal: OidcRefusal) -> str:
    """The closed code a refused step logs: the issuer's own error code, else webauth's reason for the refusal."""
    return issuer_error(issuer_refusal) or issuer_refusal.reason.value


def grant_refusal_reason(refusal: AccessTokenRefusal) -> str:
    """The closed code a refused grant logs: the issuer's own error code when it named one, else webauth's reason."""
    return issuer_error(refusal.issuer_refusal) or refusal.reason.value
