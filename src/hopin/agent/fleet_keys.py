from typing import TYPE_CHECKING, Any, ClassVar

from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource

if TYPE_CHECKING:
    from collections.abc import Mapping


class FleetKeyedSettings(BaseSettings):
    """Settings of which some fields are read from a fleet key the platform hands every app (`REALM_AI_GROUP`) and from
    no key under the class's own prefix, which an old name would otherwise still satisfy."""

    # The location of each such field, as a refusal reports it, and the key it is read from.
    environment_keys: ClassVar[Mapping[tuple[str, ...], str]] = {}

    # pydantic passes the other sources by keyword, and only the environment source is replaced.
    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        **_other_sources: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, _FleetKeyEnvironmentSource(settings_cls, cls.environment_keys))


class _FleetKeyEnvironmentSource(EnvSettingsSource):
    def __init__(self, settings_cls: type[BaseSettings], keys: Mapping[tuple[str, ...], str]) -> None:
        super().__init__(settings_cls)
        self._keys = keys

    def __call__(self) -> dict[str, Any]:
        values = super().__call__()
        for location, key in self._keys.items():
            holder = values
            for part in location[:-1]:
                holder = holder.setdefault(part, {})
            holder.pop(location[-1], None)
            if (value := self.env_vars.get(key.lower())) is not None:
                holder[location[-1]] = value
        return {name: value for name, value in values.items() if value != {}}
