"""An issuer that exists only in a test: a key it generated at run time, tokens it signed.

The relying party reaches it through the two transports the library lets a host inject,
so no test contacts a real issuer. It serves one process only.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote_plus, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from webauth.https_client import KeySetFetchError
from webauth.oidc import TokenEndpointAnswer, TokenEndpointError, TokenRequest

from hopin.auth.sign_in import IssuerTransports

if TYPE_CHECKING:
    from tests.support.sign_in_settings import SignInSettings

KEY_ID = "the-key-only-this-test-has"
TOKEN_LIFETIME_SECONDS = 300
RSA_KEY_BITS = 2048
AUTHORIZATION_CODE_BYTES = 16
REFRESH_TOKEN_BYTES = 32
ACCESS_TOKEN_BYTES = 32
GRANT_HOLD_CAP_SECONDS = 10
BACKCHANNEL_LOGOUT_EVENT = (
    "http://schemas.openid.net/event/backchannel-logout"  # NOSONAR(S5332) fixed OpenID event name, never fetched
)


@dataclass(frozen=True)
class _IssuedCode:
    nonce: str = field(repr=False)
    code_challenge: str
    claims: dict[str, object]


@dataclass(frozen=True)
class GrantHold:
    """Parks every grant of one type: `parked` is set when one waits, `release` lets them all go on,
    and `answered` is set once a parked grant has its answer."""

    parked: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)
    answered: threading.Event = field(default_factory=threading.Event)


class FakeIssuer:
    """Signs ID tokens with a generated key and answers the token and key set endpoints like Keycloak."""

    def __init__(self, sign_in: SignInSettings) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
        self._issuer = sign_in.settings.oidc_issuer
        self._client_id = sign_in.settings.oidc_client_id
        self._client_secret = sign_in.client_secret
        self._codes: dict[str, _IssuedCode] = {}
        self.refresh_token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
        self.exchanges: list[tuple[TokenRequest, TokenEndpointAnswer | None]] = []
        self.token_endpoint_is_down = False
        self.key_set_is_down = False
        self.refuses_refresh_grants = False
        self.refuses_workload_grants = False
        self._guard = threading.Lock()
        self._subjects_of_refresh_tokens: dict[str, str] = {}
        self._subjects_of_access_tokens: dict[str, str] = {}
        self._workload_tokens: set[str] = set()
        self._holds: dict[str, GrantHold] = {}

    @property
    def transports(self) -> IssuerTransports:
        return IssuerTransports(post_token_request=self.post_token_request, fetch_key_set=self.fetch_key_set)

    def authorize(
        self,
        authorization_url: str,
        *,
        subject: str,
        groups: list[str] | str | None,
        username: str,
    ) -> str:
        """The callback query the issuer sends the browser back with after a person signs in."""
        parameters = parse_qs(urlsplit(authorization_url).query)
        claims: dict[str, object] = {"sub": subject, "preferred_username": username}
        if groups is not None:
            claims["groups"] = groups
        code = secrets.token_urlsafe(AUTHORIZATION_CODE_BYTES)
        self._codes[code] = _IssuedCode(
            nonce=parameters["nonce"][0],
            code_challenge=parameters["code_challenge"][0],
            claims=claims,
        )
        return f"code={code}&state={parameters['state'][0]}"

    def refresh_token_for(self, subject: str) -> str:
        """A refresh token the issuer honours once, for the subject's next grant."""
        refresh_token = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
        with self._guard:
            self._subjects_of_refresh_tokens[refresh_token] = subject
        return refresh_token

    def subject_of_access_token(self, access_token: str) -> str | None:
        with self._guard:
            return self._subjects_of_access_tokens.get(access_token)

    def is_workload_token(self, access_token: str) -> bool:
        with self._guard:
            return access_token in self._workload_tokens

    def hold(self, grant_type: str) -> GrantHold:
        """Park every later grant of this type until the hold is released."""
        hold = GrantHold()
        self._holds[grant_type] = hold
        return hold

    def logout_token(self, subject: str, *, audience: str | None = None, issued_at: int | None = None) -> str:
        """The Logout Token the issuer posts to a client's back-channel logout URL."""
        issued = int(time.time()) if issued_at is None else issued_at
        claims = {
            "iss": self._issuer,
            "aud": self._client_id if audience is None else audience,
            "iat": issued,
            "exp": issued + TOKEN_LIFETIME_SECONDS,
            "sub": subject,
            "events": {BACKCHANNEL_LOGOUT_EVENT: {}},
        }
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": KEY_ID, "typ": "logout+jwt"})

    def post_token_request(self, request: TokenRequest) -> TokenEndpointAnswer:
        if self.token_endpoint_is_down:
            self.exchanges.append((request, None))
            message = "the token endpoint of a test issuer is down"
            raise TokenEndpointError(message)
        answer = self._answer(request)
        self.exchanges.append((request, answer))
        return answer

    def _answer(self, request: TokenRequest) -> TokenEndpointAnswer:
        if not self._is_authenticated(request.authorization.get_secret_value()):
            return self._refusal(HTTPStatus.UNAUTHORIZED, "invalid_client")
        grant_type = request.form["grant_type"]
        hold = self._holds.get(grant_type)
        if hold is None:
            return self._grant(grant_type, request)
        hold.parked.set()
        assert hold.release.wait(GRANT_HOLD_CAP_SECONDS), f"a held {grant_type} grant was never released"
        answer = self._grant(grant_type, request)
        hold.answered.set()
        return answer

    def _grant(self, grant_type: str, request: TokenRequest) -> TokenEndpointAnswer:
        if grant_type == "refresh_token":
            return self._renewal(request.form["refresh_token"])
        if grant_type == "client_credentials":
            return self._workload_access()
        return self._code_exchange(request)

    def _renewal(self, refresh_token: str) -> TokenEndpointAnswer:
        with self._guard:
            subject = self._subjects_of_refresh_tokens.pop(refresh_token, None)
            if subject is None or self.refuses_refresh_grants:
                return self._refusal(HTTPStatus.BAD_REQUEST, "invalid_grant")
            rotated = secrets.token_urlsafe(REFRESH_TOKEN_BYTES)
            self._subjects_of_refresh_tokens[rotated] = subject
            access_token = secrets.token_urlsafe(ACCESS_TOKEN_BYTES)
            self._subjects_of_access_tokens[access_token] = subject
        return self._bearer_answer(access_token, refresh_token=rotated)

    def _workload_access(self) -> TokenEndpointAnswer:
        if self.refuses_workload_grants:
            return self._refusal(HTTPStatus.BAD_REQUEST, "unauthorized_client")
        access_token = secrets.token_urlsafe(ACCESS_TOKEN_BYTES)
        with self._guard:
            self._workload_tokens.add(access_token)
        return self._bearer_answer(access_token)

    @staticmethod
    def _bearer_answer(access_token: str, *, refresh_token: str | None = None) -> TokenEndpointAnswer:
        body = {"access_token": access_token, "token_type": "Bearer", "expires_in": TOKEN_LIFETIME_SECONDS}
        if refresh_token is not None:
            body["refresh_token"] = refresh_token
        return TokenEndpointAnswer(HTTPStatus.OK, json.dumps(body).encode())

    def _code_exchange(self, request: TokenRequest) -> TokenEndpointAnswer:
        issued = self._codes.pop(request.form["code"], None)
        if issued is None or not self._proves_possession(issued, request.form["code_verifier"]):
            return self._refusal(HTTPStatus.BAD_REQUEST, "invalid_grant")
        body = {"id_token": self._sign(issued), "refresh_token": self.refresh_token}
        return TokenEndpointAnswer(HTTPStatus.OK, json.dumps(body).encode())

    def fetch_key_set(self, url: str) -> bytes:
        del url
        if self.key_set_is_down:
            message = "the key set endpoint of a test issuer is down"
            raise KeySetFetchError(message)
        public_key = jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        return json.dumps({"keys": [{**public_key, "kid": KEY_ID, "alg": "RS256", "use": "sig"}]}).encode()

    def _is_authenticated(self, authorization: str) -> bool:
        scheme, _, encoded = authorization.partition(" ")
        if scheme != "Basic":
            return False
        client_id, _, client_secret = base64.b64decode(encoded).decode().partition(":")
        return (unquote_plus(client_id), unquote_plus(client_secret)) == (self._client_id, self._client_secret)

    @staticmethod
    def _refusal(status: HTTPStatus, error: str) -> TokenEndpointAnswer:
        return TokenEndpointAnswer(status, json.dumps({"error": error}).encode())

    @staticmethod
    def _proves_possession(issued: _IssuedCode, code_verifier: str) -> bool:
        digest = hashlib.sha256(code_verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == issued.code_challenge

    def _sign(self, issued: _IssuedCode) -> str:
        issued_at = int(time.time())
        claims = {
            "iss": self._issuer,
            "aud": self._client_id,
            "iat": issued_at,
            "exp": issued_at + TOKEN_LIFETIME_SECONDS,
            "nonce": issued.nonce,
            **issued.claims,
        }
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": KEY_ID})
