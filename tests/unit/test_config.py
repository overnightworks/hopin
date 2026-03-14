import os
from unittest.mock import patch

from openstream.config.constants import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_VIEWERS_PER_ROOM,
    ROOM_ID_LENGTH,
    STUN_SERVER_URL,
    RoomRole,
    SignalType,
)
from openstream.config.settings import ServerSettings


class TestConstants:
    def test_default_host_binds_all_interfaces(self):
        assert DEFAULT_HOST == "0.0.0.0"

    def test_default_port_is_8080(self):
        assert DEFAULT_PORT == 8080

    def test_room_id_length_is_8(self):
        assert ROOM_ID_LENGTH == 8

    def test_max_viewers_per_room_is_50(self):
        assert MAX_VIEWERS_PER_ROOM == 50

    def test_stun_server_uses_google(self):
        assert "stun" in STUN_SERVER_URL


class TestSignalType:
    def test_all_signal_types_are_strings(self):
        for signal_type in SignalType:
            assert isinstance(signal_type.value, str)

    def test_contains_required_types(self):
        required = {"offer", "answer", "ice_candidate", "join", "leave", "error"}
        actual = {s.value for s in SignalType}
        assert required.issubset(actual)


class TestRoomRole:
    def test_streamer_role_exists(self):
        assert RoomRole.STREAMER.value == "streamer"

    def test_viewer_role_exists(self):
        assert RoomRole.VIEWER.value == "viewer"

    def test_only_two_roles(self):
        assert len(RoomRole) == 2


class TestServerSettings:
    def test_defaults(self):
        settings = ServerSettings()
        assert settings.host == DEFAULT_HOST
        assert settings.port == DEFAULT_PORT
        assert settings.stun_server == STUN_SERVER_URL
        assert settings.max_viewers_per_room == MAX_VIEWERS_PER_ROOM

    def test_override_via_environment(self):
        env = {
            "OPENSTREAM_HOST": "127.0.0.1",
            "OPENSTREAM_PORT": "9090",
            "OPENSTREAM_MAX_VIEWERS_PER_ROOM": "10",
        }
        with patch.dict(os.environ, env):
            settings = ServerSettings()
            assert settings.host == "127.0.0.1"
            assert settings.port == 9090
            assert settings.max_viewers_per_room == 10
