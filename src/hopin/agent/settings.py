import re
from typing import Final

from agent_runtime_client.config import AgentConfig  # noqa: TC002 - pydantic resolves the annotation at runtime
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# RFC 6749 section 3.3: one or more printable ASCII characters other than space, `"` and `\`.
SCOPE_NAME: Final = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")


class AgentSettings(BaseSettings):
    # Read like the server's own settings, and a refusal never echoes an input.
    model_config = SettingsConfigDict(env_prefix="HOPIN_", env_nested_delimiter="__", hide_input_in_errors=True)

    agent: AgentConfig

    @field_validator("agent")
    @classmethod
    def _scope_is_a_scope_name(cls, agent: AgentConfig) -> AgentConfig:
        # The scope also goes into the sign-in request, which the client's own check does not cover.
        if SCOPE_NAME.fullmatch(agent.workload_scope) is None:
            raise ValueError("workload_scope must be one RFC 6749 scope token")
        return agent
