import unicodedata
from typing import Annotated, Final

from pydantic import AfterValidator
from pydantic_settings import BaseSettings, SettingsConfigDict

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


class ParticipationSettings(BaseSettings):
    """Who may add an agent that has an account of its own: an owner's subject mapped to the full path of the group
    whose members may add that owner's agent, compared exactly. Unset or empty, nobody adds."""

    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", hide_input_in_errors=True)

    agent_adders: dict[str, GroupPath] = {}
