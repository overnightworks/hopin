"""The TURN provider behind the transport port, and the server settings that switch TURN on."""

import json
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hopin.web.turn_relay import TurnAnswer

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    import urllib3

STUN_ENTRY = {"urls": ["stun:stun.cloudflare.com:3478"]}
RELAY_URLS = [
    "turn:turn.cloudflare.com:3478?transport=udp",
    "turn:turn.cloudflare.com:3478?transport=tcp",
    "turns:turn.cloudflare.com:5349?transport=tcp",
    "turns:turn.cloudflare.com:443?transport=tcp",
]
PROVIDER_USERNAME = "provider-user"
PROVIDER_CREDENTIAL = "provider-credential"


def provider_answer(urls=RELAY_URLS, *, username=PROVIDER_USERNAME, credential=PROVIDER_CREDENTIAL):
    return {"iceServers": [STUN_ENTRY, {"urls": urls, "username": username, "credential": credential}]}


@dataclass
class FakeTransport:
    """The provider's side of the seam: records each call and answers the scenario."""

    status: int = 201
    body: bytes | Callable[[], bytes] = field(
        default_factory=lambda: json.dumps(provider_answer()).encode(), repr=False
    )
    calls: list[tuple[str, Mapping[str, str], bytes]] = field(default_factory=list, repr=False)

    def __call__(self, url: str, *, headers: Mapping[str, str], body: bytes, timeout: urllib3.Timeout) -> TurnAnswer:
        self.calls.append((url, headers, body))
        return TurnAnswer(self.status, self.body() if callable(self.body) else self.body)


@dataclass
class TurnKeys:
    key_id: str = field(default_factory=lambda: secrets.token_hex(16))
    api_token: str = field(default_factory=lambda: secrets.token_urlsafe(48), repr=False)


def turn_on(directory: Path, keys: TurnKeys | None = None) -> dict[str, str | Path]:
    """The server settings that switch TURN on, with the key files written into `directory`."""
    keys = keys or TurnKeys()
    (directory / "turn-key-id").write_text(keys.key_id + "\n")
    (directory / "turn-api-token").write_text(keys.api_token + "\n")
    return {
        "turn_provider": "cloudflare",
        "turn_key_id_file": directory / "turn-key-id",
        "turn_api_token_file": directory / "turn-api-token",
    }
