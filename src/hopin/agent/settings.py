from typing import ClassVar, Final

from agents.config import AgentConfig  # noqa: TC002 - pydantic resolves the annotation at runtime
from pydantic_settings import SettingsConfigDict

from hopin.agent.fleet_keys import FleetKeyedSettings

# The fleet's key for the AI gateway's group, which the platform's Deployment hands every app.
AI_GROUP_KEY: Final = "REALM_AI_GROUP"


class AgentSettings(FleetKeyedSettings):
    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", env_nested_delimiter="__", hide_input_in_errors=True)

    environment_keys: ClassVar = {("agent", "api_group"): AI_GROUP_KEY}

    agent: AgentConfig
