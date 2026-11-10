import unicodedata
from typing import Annotated, Final
from urllib.parse import urlsplit

from pydantic import AfterValidator, PlainValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.participation.figures import FigureSource, path_refusal

FORBIDDEN_PATH_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _full_group_path(path: str) -> str:
    """The path when it is a Keycloak group path such as `/apps/name`: non-empty segments, no whitespace around one,
    no control, format or line-separator character."""
    segments = path.split("/")
    if segments[0] != "" or len(segments) < 2 or not all(segments[1:]):
        raise ValueError("a group path looks like /apps/name")
    if any(segment != segment.strip() for segment in segments):
        raise ValueError("a group path has no whitespace around a segment")
    if any(unicodedata.category(character) in FORBIDDEN_PATH_CATEGORIES for character in path):
        raise ValueError("a group path has no control, format or line-separator character")
    return path


type GroupPath = Annotated[str, AfterValidator(_full_group_path)]


def _figure_source(prefix: object) -> FigureSource:
    """The source an allowed prefix names: https, a lowercase host with no port or user, no query or fragment, and a
    path that starts and ends with `/` and follows the figure's path rule; no whitespace or non-printable character
    anywhere."""
    message = "a figure source is an https prefix with a lowercase host and a path that ends with /"
    # `urlsplit` drops surrounding space, tabs and newlines, so they are refused before it reads the prefix.
    if not isinstance(prefix, str) or not prefix.isprintable() or any(map(str.isspace, prefix)):
        raise ValueError(message)
    if "?" in prefix or "#" in prefix:
        raise ValueError(message)
    parts = urlsplit(prefix)
    host, path = parts.netloc, parts.path
    if (
        parts.scheme != "https"
        or not host
        or host != host.lower()
        or not {":", "@"}.isdisjoint(host)
        or not path.startswith("/")
        or not path.endswith("/")
        or path_refusal(path.removesuffix("/")) is not None
    ):
        raise ValueError(message)
    return FigureSource(host, path)


type FigureSourcePrefix = Annotated[FigureSource, PlainValidator(_figure_source)]


class ParticipationSettings(BaseSettings):
    """Who may add an agent that has an account of its own: an owner's subject mapped to the full path of the group
    whose members may add that owner's agent, compared exactly. Unset or empty, nobody adds.

    Where an agent's figure may come from: the URL prefixes HopIn fetches figures under. Unset or empty, no figure is
    fetched."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    agent_adders: dict[str, GroupPath] = {}
    figure_sources: tuple[FigureSourcePrefix, ...] = ()
