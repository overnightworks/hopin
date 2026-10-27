import itertools
from ipaddress import IPv4Address
from typing import TYPE_CHECKING, Any

import pytest

from tests.e2e.support.server import LOOPBACK_HOST
from tests.support.signed_in import parsed_set_cookies

if TYPE_CHECKING:
    from webauth.config import WebAuthConfig

# Every e2e person gets an address of their own from this private range, as behind a proxy.
PERSON_ADDRESS_BASE = IPv4Address("10.0.0.0")
person_address_offsets = itertools.count(1)

DESKTOP = {"viewport": {"width": 1280, "height": 800}}
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}

FORM_FACTORS = [pytest.param(DESKTOP, id="desktop"), pytest.param(PHONE, id="phone")]

SMALL_PHONE_LANDSCAPE = {"viewport": {"width": 568, "height": 320}, "is_mobile": True, "has_touch": True}


def _playwright_cookie(set_cookie_header: str, config: WebAuthConfig) -> dict[str, Any]:
    ((name, morsel),) = parsed_set_cookies([set_cookie_header])
    expires = int(config.wall_clock()) + int(morsel["max-age"])
    # The URL form of add_cookies is refused for a __Host- name, so the cookie goes in by domain and path.
    return {
        "name": name,
        "value": morsel.value,
        "domain": LOOPBACK_HOST,
        "path": morsel["path"],
        "secure": bool(morsel["secure"]),
        "httpOnly": bool(morsel["httponly"]),
        "sameSite": morsel["samesite"].capitalize(),
        "expires": expires,
    }
