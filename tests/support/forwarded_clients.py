import json

import pytest

TRUSTED_PROXY = "172.31.250.1"
UNTRUSTED_PEER = "192.0.2.77"
FORWARDED_CLIENT = "198.51.100.7"
FORGED_ENTRY = "203.0.113.99"

# A peer's address, the X-Forwarded-For it sends and the client address the request's line has to name.
FORWARDED_CLIENT_CASES = [
    pytest.param(TRUSTED_PROXY, FORWARDED_CLIENT, FORWARDED_CLIENT, id="trusted_proxy_forwarding"),
    pytest.param(
        TRUSTED_PROXY, f"{FORGED_ENTRY}, {FORWARDED_CLIENT}", FORWARDED_CLIENT, id="trusted_proxy_with_forged_entry"
    ),
    pytest.param(TRUSTED_PROXY, None, TRUSTED_PROXY, id="trusted_proxy_forwarding_nothing"),
    pytest.param(UNTRUSTED_PEER, FORGED_ENTRY, UNTRUSTED_PEER, id="untrusted_peer_forging"),
    pytest.param(UNTRUSTED_PEER, None, UNTRUSTED_PEER, id="untrusted_peer_forwarding_nothing"),
]


def forwarding_headers(forwarded_for: str | None) -> dict[str, str]:
    return {} if forwarded_for is None else {"x-forwarded-for": forwarded_for}


def lines_of_event(output: str, event: str) -> list[dict]:
    """The JSON lines of the output that carry the event."""
    lines = [json.loads(line) for line in output.splitlines()]
    return [line for line in lines if line["event"] == event]
