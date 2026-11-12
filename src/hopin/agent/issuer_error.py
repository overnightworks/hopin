from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from webauth.oidc import OidcRefusal


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
