from pydantic_settings import BaseSettings

from openstream.config.constants import DEFAULT_HOST, DEFAULT_PORT, MAX_VIEWERS_PER_ROOM, STUN_SERVER_URL


class ServerSettings(BaseSettings):
    model_config = {"env_prefix": "OPENSTREAM_"}

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    stun_server: str = STUN_SERVER_URL
    max_viewers_per_room: int = MAX_VIEWERS_PER_ROOM
