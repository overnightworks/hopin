import re
from urllib.parse import urlsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.config.secret_files import StartRefusedError
from hopin.skat.vocabulary import CALL_MODE_QUERY, TABLE_PATH_PATTERN

HTTPS_PORT = 443
HOST_PATTERN = re.compile(r"[a-z0-9.-]+")
DEFAULT_PORTS = {"https": HTTPS_PORT, "http": 80}

type BrowserOrigin = tuple[str, str, int]


def canonical_origin(url: str) -> BrowserOrigin | None:
    """The scheme, the ASCII host without a trailing dot and the effective port of an http(s) URL, which is what a
    browser compares; nothing when the URL has none."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = parts.hostname
        port = parts.port
        if scheme not in DEFAULT_PORTS or not host:
            return None
        return scheme, host.encode("idna").decode("ascii").rstrip("."), port or DEFAULT_PORTS[scheme]
    except ValueError:
        return None


def _require_browser_origin_form(value: str) -> str:
    """The value when it is a browser's serialisation of an https origin, which the page compares byte for byte with
    the origin of a message."""
    parts = urlsplit(value)
    host = parts.hostname or ""
    netloc = host if parts.port is None else f"{host}:{parts.port}"
    written_as_an_origin = value == f"https://{netloc}" and parts.netloc == netloc
    is_a_host = (
        HOST_PATTERN.fullmatch(host) is not None and not host.endswith(".") and canonical_origin(value) is not None
    )
    if not (written_as_an_origin and is_a_host and parts.port != HTTPS_PORT):
        raise ValueError("an https origin has a lowercase ASCII host and an optional port other than 443, no path")
    return value


class SkatSettings(BaseSettings):
    """Where Skat's tables live: the https origin whose frame a call shows on its stage. Unset, there is no Skat."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_SKAT_", hide_input_in_errors=True)

    origin: str | None = None

    @field_validator("origin")
    @classmethod
    def _an_origin_in_a_browser_form(cls, value: str | None) -> str | None:
        return None if value is None else _require_browser_origin_form(value)


def check_origin_differs(skat_origin: str, public_origin: str) -> None:
    """Refuse Skat's origin when it is HopIn's own: a framed page with scripts on HopIn's origin lifts its sandbox."""
    if canonical_origin(skat_origin) == canonical_origin(public_origin):
        raise StartRefusedError("HOPIN_SKAT_ORIGIN must be another origin than HOPIN_PUBLIC_ORIGIN")


def table_source(origin: str, link: str) -> str | None:
    """The address the frame loads for a pasted link to a table of the origin, or nothing when the link is none: the
    origin must equal the configured one, carry no userinfo and the path must be exactly a table's."""
    try:
        parts = urlsplit(link)
    except ValueError:
        return None
    if (
        TABLE_PATH_PATTERN.fullmatch(parts.path) is None
        or "@" in parts.netloc
        or canonical_origin(link) != canonical_origin(origin)
    ):
        return None
    return f"{origin}{parts.path}?{CALL_MODE_QUERY}"
