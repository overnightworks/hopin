import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, WrapValidator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.config.secret_files import StartRefusedError
from hopin.stage.vocabulary import (
    APP_NAME_PATTERN,
    CALL_MODE_QUERY,
    CONTEXT_KEY_PATTERN,
    CONTEXT_PLACEHOLDER,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import ValidatorFunctionWrapHandler

HTTPS_PORT = 443
HOST_PATTERN = re.compile(r"[a-z0-9.-]+")
DEFAULT_PORTS = {"https": HTTPS_PORT, "http": 80}

MAX_TITLE_LENGTH = 64

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


def _require_frame_path(value: str) -> str:
    """The value when it is a path to append to an origin: it starts with a slash, holds the context's placeholder
    once, and has no query, fragment or dot segment, since the frame's address adds its own query."""
    segments = value.split("/")
    if (
        not value.startswith("/")
        or value.count(CONTEXT_PLACEHOLDER) != 1
        or "?" in value
        or "#" in value
        or "." in segments
        or ".." in segments
    ):
        raise ValueError("a frame path starts with a slash, holds the placeholder once and has no query or dot segment")
    return value


class StageApp(BaseModel):
    """An app whose tables a call may show on its stage, in a frame of the app's own origin."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    # Stable: the person-key label of the app's chairs is built from it.
    name: str
    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)
    origin: str
    frame_path: str

    @field_validator("name")
    @classmethod
    def _a_name_in_the_app_form(cls, value: str) -> str:
        if APP_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("an app name is a lowercase letter, then lowercase letters, digits or hyphens, 32 at most")
        return value

    @field_validator("origin")
    @classmethod
    def _an_origin_in_a_browser_form(cls, value: str) -> str:
        return _require_browser_origin_form(value)

    @field_validator("frame_path")
    @classmethod
    def _a_path_with_one_placeholder(cls, value: str) -> str:
        return _require_frame_path(value)


def _a_registry_without_repeats(value: object, handler: ValidatorFunctionWrapHandler) -> tuple[StageApp, ...]:
    """The registry when every entry is well formed and no name or origin repeats; otherwise one refusal that holds
    nothing of the value."""
    try:
        apps: tuple[StageApp, ...] = handler(value)
    except ValidationError:
        raise ValueError("malformed") from None
    if len({app.name for app in apps}) != len(apps) or len({app.origin for app in apps}) != len(apps):
        raise ValueError("a name or an origin repeats")
    return apps


class StageSettings(BaseSettings):
    """The apps a call may show on its stage. Unset or empty, there is no stage."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_STAGE_", hide_input_in_errors=True)

    apps: Annotated[tuple[StageApp, ...], WrapValidator(_a_registry_without_repeats)] = ()


def check_origins_differ(apps: Sequence[StageApp], public_origin: str) -> None:
    """Refuse an app whose origin is HopIn's own: a framed page with scripts on HopIn's origin lifts its sandbox."""
    for app in apps:
        if canonical_origin(app.origin) == canonical_origin(public_origin):
            raise StartRefusedError("HOPIN_STAGE_APPS must name other origins than HOPIN_PUBLIC_ORIGIN")


def frame_source(app: StageApp, context: str) -> str:
    """The address the frame of the app's table loads."""
    return f"{app.origin}{app.frame_path.replace(CONTEXT_PLACEHOLDER, context)}?{CALL_MODE_QUERY}"


@dataclass(frozen=True)
class LinkedContext:
    """A table a pasted link names: its app, its key and the address its frame loads."""

    app: StageApp
    context: str
    source: str


def linked_context(apps: Sequence[StageApp], link: str) -> LinkedContext | None:
    """The table a pasted link names, or nothing when the link is none: the origin must equal an app's, carry no
    userinfo, and the path must be exactly the app's frame path with a context key."""
    try:
        parts = urlsplit(link)
    except ValueError:
        return None
    if "@" in parts.netloc:
        return None
    linked_origin = canonical_origin(link)
    for app in apps:
        prefix, suffix = app.frame_path.split(CONTEXT_PLACEHOLDER)
        path = re.fullmatch(f"{re.escape(prefix)}({CONTEXT_KEY_PATTERN}){re.escape(suffix)}", parts.path)
        if path is not None and linked_origin == canonical_origin(app.origin):
            return LinkedContext(app, path[1], frame_source(app, path[1]))
    return None
