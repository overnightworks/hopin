from typing import Annotated
from urllib.parse import urlsplit

from pydantic import PlainValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

from hopin.participation.figures import FigureSource, path_refusal


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
    """Where an agent's figure may come from: the URL prefixes HopIn fetches figures under. Unset or empty, no figure is
    fetched."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    figure_sources: tuple[FigureSourcePrefix, ...] = ()
