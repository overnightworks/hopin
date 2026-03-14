from pydantic_settings import BaseSettings

from hopin.config.constants import DEFAULT_HOST, DEFAULT_PORT, MAX_PARTICIPANTS_PER_ROOM, STUN_SERVER_URL


class ServerSettings(BaseSettings):
    model_config = {"env_prefix": "HOPIN_"}

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    stun_server: str = STUN_SERVER_URL
    max_participants_per_room: int = MAX_PARTICIPANTS_PER_ROOM
