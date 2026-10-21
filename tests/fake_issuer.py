"""An issuer that exists only in a test: a key it generated at run time, tokens it signed.

The relying party reaches it through the two transports the library lets a host inject,
so no test contacts a real issuer. It serves one process only.
"""

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from webauth.https_client import KeySetFetchError
from webauth.oidc import TokenEndpointAnswer, TokenEndpointError, TokenRequest

from hopin.auth.sign_in import IssuerTransports
from tests.sign_in_settings import SignInSettings

KEY_ID = "the-key-only-this-test-has"
TOKEN_LIFETIME_SECONDS = 300
RSA_KEY_BITS = 2048
AUTHORIZATION_CODE_BYTES = 16


@dataclass(frozen=True)
class _IssuedCode:
    nonce: str
    code_challenge: str
    claims: dict[str, object]


class FakeIssuer:
    """Signs ID tokens with a generated key and answers the token and key set endpoints like Keycloak."""

    def __init__(self, sign_in: SignInSettings) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
        self._issuer = sign_in.settings.oidc_issuer
        self._client_id = sign_in.settings.oidc_client_id
        self._client_secret = sign_in.client_secret
        self._codes: dict[str, _IssuedCode] = {}
        self.exchanges: list[tuple[TokenRequest, TokenEndpointAnswer]] = []
        self.token_endpoint_is_down = False
        self.key_set_is_down = False

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

    def post_token_request(self, request: TokenRequest) -> TokenEndpointAnswer:
        if self.token_endpoint_is_down:
            message = "the token endpoint of a test issuer is down"
            raise TokenEndpointError(message)
        answer = self._answer(request)
        self.exchanges.append((request, answer))
        return answer

    def _answer(self, request: TokenRequest) -> TokenEndpointAnswer:
        if request.authorization.get_secret_value() != self._basic_authorization():
            return self._refusal(HTTPStatus.UNAUTHORIZED, "invalid_client")
        issued = self._codes.pop(request.form["code"], None)
        if issued is None or not self._proves_possession(issued, request.form["code_verifier"]):
            return self._refusal(HTTPStatus.BAD_REQUEST, "invalid_grant")
        return TokenEndpointAnswer(HTTPStatus.OK, json.dumps({"id_token": self._sign(issued)}).encode())

    def fetch_key_set(self, url: str) -> bytes:
        del url
        if self.key_set_is_down:
            message = "the key set endpoint of a test issuer is down"
            raise KeySetFetchError(message)
        public_key = jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        return json.dumps({"keys": [{**public_key, "kid": KEY_ID, "alg": "RS256", "use": "sig"}]}).encode()

    def _basic_authorization(self) -> str:
        credentials = f"{self._client_id}:{self._client_secret}".encode()
        return "Basic " + base64.b64encode(credentials).decode()

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
