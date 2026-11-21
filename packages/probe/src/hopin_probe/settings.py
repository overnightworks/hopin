"""The probe's configuration, read from `HOPIN_PROBE_*` environment values; a failure names a field, not its value."""

import json
from pathlib import Path
from typing import Annotated

from pydantic import Field, HttpUrl, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from hopin_probe.checks import Stage, StartFailure

DEFAULT_VOICE_FILE = Path("/opt/piper-voice/de_DE-thorsten-medium.onnx")


class ProbeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HOPIN_PROBE_", hide_input_in_errors=True)

    origin: HttpUrl
    username: str = Field(min_length=1)
    password_file: Path
    # The test agents' names, a JSON list; the probe reads the first.
    agents: Annotated[list[str], NoDecode] = Field(min_length=1)
    voice_file: Path = DEFAULT_VOICE_FILE
    join_seconds: int = Field(default=60, gt=0)
    answer_seconds: int = Field(default=30, gt=0)

    @field_validator("agents", mode="before")
    @classmethod
    def _agents_from_json(cls, value: str) -> list[str]:
        return json.loads(value)  # type: ignore[no-any-return]

    @property
    def base_url(self) -> str:
        return str(self.origin).rstrip("/")

    def read_password(self) -> SecretStr:
        try:
            password = self.password_file.read_text().strip()
        except FileNotFoundError:
            raise StartFailure(Stage.SETTINGS, "password_file_missing") from None
        if not password:
            raise StartFailure(Stage.SETTINGS, "password_file_empty")
        return SecretStr(password)


def load_settings() -> ProbeSettings:
    try:
        return ProbeSettings()
    except ValidationError as failure:
        field = str(failure.errors()[0]["loc"][0])
        raise StartFailure(Stage.SETTINGS, "invalid", field=field) from None
