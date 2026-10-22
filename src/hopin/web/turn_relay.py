import contextlib
import json
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import urllib3
from pydantic import BaseModel, Field, SecretStr, StrictStr

from hopin.config.constants import TURN_CREDENTIAL_TTL_SECONDS, TurnProvider
from hopin.config.secret_files import StartRefusedError, read_secret_file
from hopin.web.ice_servers import IceServer, TurnRelay, TurnRelayFailure, TurnRelayUnavailableError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from hopin.config.settings import ServerSettings

CLOUDFLARE_ENDPOINT = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"
# The page waits for this answer as a call starts.
CONNECT_TIMEOUT_SECONDS = 3
READ_TIMEOUT_SECONDS = 5
# Browsers cannot reach Cloudflare's alternate port 53, and Cloudflare advises filtering it out.
UNREACHABLE_BROWSER_PORT = "53"
KEY_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
# RFC 6750 b64token: ASCII only, so the token is safe in a header.
API_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9._~+/-]+=*")


@dataclass(frozen=True)
class TurnAnswer:
    status: int
    body: bytes


class TurnCredentialsPost(Protocol):
    def __call__(self, url: str, *, headers: Mapping[str, str], body: bytes, timeout: urllib3.Timeout) -> TurnAnswer:
        """Raises TurnRelayUnavailableError when no answer arrives."""


_unlogged: ContextVar[bool] = ContextVar("hopin_turn_unlogged", default=False)


@contextlib.contextmanager
def _urllib3_unlogged() -> Iterator[None]:
    token = _unlogged.set(True)
    try:
        yield
    finally:
        _unlogged.reset(token)


def _outside_a_call(record: logging.LogRecord) -> bool:
    del record
    return not _unlogged.get()


def _urllib3_module_names() -> Iterator[str]:
    """Read from the installed package's files, so no module is imported;
    each is its logger's name, since urllib3 logs only on `__name__`."""
    package = Path(urllib3.__file__).parent
    for source in package.rglob("*.py"):
        module = source.relative_to(package.parent).with_suffix("")
        yield ".".join(module.parent.parts if module.name == "__init__" else module.parts)


# urllib3 logs the request path, which holds the key ID, and the origin's header text. A logger's
# filter sees only records logged on that logger, not on its children, so every module's logger gets one.
for _module_name in _urllib3_module_names():
    logging.getLogger(_module_name).addFilter(_outside_a_call)


def post_over_https(url: str, *, headers: Mapping[str, str], body: bytes, timeout: urllib3.Timeout) -> TurnAnswer:
    try:
        with _urllib3_unlogged(), urllib3.PoolManager() as pool:
            response = pool.request(
                "POST", url, body=body, headers=dict(headers), timeout=timeout, retries=False, redirect=False
            )
    # A refused connection is a NewConnectionError, which urllib3 derives from its connect timeout.
    except urllib3.exceptions.NewConnectionError:
        raise TurnRelayUnavailableError(TurnRelayFailure.UNREACHABLE) from None
    except urllib3.exceptions.TimeoutError:
        raise TurnRelayUnavailableError(TurnRelayFailure.TIMED_OUT) from None
    except urllib3.exceptions.HTTPError:
        raise TurnRelayUnavailableError(TurnRelayFailure.UNREACHABLE) from None
    return TurnAnswer(status=response.status, body=response.data)


TURN_OVER_HTTPS: TurnCredentialsPost = post_over_https


class _ProviderEntry(BaseModel):
    urls: list[StrictStr] = Field(min_length=1)
    username: StrictStr | None = None
    credential: StrictStr | None = None


class _ProviderAnswer(BaseModel):
    ice_servers: list[_ProviderEntry] = Field(alias="iceServers")


def _port_of(url: str) -> str | None:
    host_and_port = url.partition(":")[2].partition("?")[0]
    host, separator, port = host_and_port.rpartition(":")
    return port if host and separator else None


def _is_relay_url(url: str) -> bool:
    return url.startswith(("turn:", "turns:"))


def _relay_entries(answer: _ProviderAnswer) -> list[IceServer]:
    entries: list[IceServer] = []
    for entry in answer.ice_servers:
        if not any(_is_relay_url(url) for url in entry.urls):
            if all(url.startswith("stun:") for url in entry.urls):
                continue
            raise ValueError("an entry that is neither STUN nor TURN")
        if not all(_is_relay_url(url) for url in entry.urls) or entry.username is None or entry.credential is None:
            raise ValueError("a TURN entry that is mixed or has no credential")
        urls = [url for url in entry.urls if _port_of(url) != UNREACHABLE_BROWSER_PORT]
        if urls:
            entries.append(IceServer(urls=urls, username=entry.username, credential=entry.credential))
    if not entries:
        raise ValueError("no TURN URL left")
    return entries


@dataclass(frozen=True)
class CloudflareTurnRelay:
    key_id: str = field(repr=False)
    api_token: SecretStr = field(repr=False)
    post: TurnCredentialsPost

    def relay_servers(self) -> list[IceServer]:
        answer = self.post(
            CLOUDFLARE_ENDPOINT.format(key_id=self.key_id),
            headers={
                "Authorization": f"Bearer {self.api_token.get_secret_value()}",
                "Content-Type": "application/json",
            },
            body=json.dumps({"ttl": TURN_CREDENTIAL_TTL_SECONDS}).encode(),
            timeout=urllib3.Timeout(connect=CONNECT_TIMEOUT_SECONDS, read=READ_TIMEOUT_SECONDS),
        )
        if not 200 <= answer.status < 300:
            raise TurnRelayUnavailableError(TurnRelayFailure.REFUSED, answer.status)
        try:
            return _relay_entries(_ProviderAnswer.model_validate_json(answer.body))
        except ValueError:
            raise TurnRelayUnavailableError(TurnRelayFailure.MALFORMED) from None


def _checked(value: SecretStr, pattern: re.Pattern[str], setting: str) -> None:
    if not pattern.fullmatch(value.get_secret_value()):
        raise StartRefusedError(f"{setting} holds a value that is not accepted") from None


def build_turn_relay(settings: ServerSettings, post: TurnCredentialsPost) -> TurnRelay | None:
    if settings.turn_provider is TurnProvider.OFF:
        return None
    # The settings validator refuses a missing file when TURN is on.
    key_id_file = cast("Path", settings.turn_key_id_file)
    api_token_file = cast("Path", settings.turn_api_token_file)
    key_id = read_secret_file(key_id_file, "turn_key_id_file")
    _checked(key_id, KEY_ID_PATTERN, "turn_key_id_file")
    api_token = read_secret_file(api_token_file, "turn_api_token_file")
    _checked(api_token, API_TOKEN_PATTERN, "turn_api_token_file")
    return CloudflareTurnRelay(key_id=key_id.get_secret_value(), api_token=api_token, post=post)
