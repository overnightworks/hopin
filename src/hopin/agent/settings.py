from agent_runtime_client.config import AgentConfig  # noqa: TC002 - pydantic resolves the annotation at runtime
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", env_nested_delimiter="__", hide_input_in_errors=True)

    agent: AgentConfig
