import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from hopin.config.constants import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_PARTICIPANTS_PER_ROOM,
    MIN_PARTICIPANTS_PER_ROOM,
    ROOM_ID_LENGTH,
    STUN_SERVER_URL,
    SignalType,
)
from hopin.config.settings import ServerSettings


class TestConstants:
    def test_default_host_binds_all_interfaces(self):
        assert DEFAULT_HOST == "0.0.0.0"

    def test_default_port_is_8080(self):
        assert DEFAULT_PORT == 8080

    def test_room_id_length_is_8(self):
        assert ROOM_ID_LENGTH == 8

    def test_max_participants_per_room_is_6(self):
        assert MAX_PARTICIPANTS_PER_ROOM == 6

    def test_stun_server_defaults_to_cloudflare(self):
        assert STUN_SERVER_URL == "stun:stun.cloudflare.com:3478"


class TestSignalType:
    def test_all_signal_types_are_strings(self):
        for signal_type in SignalType:
            assert isinstance(signal_type.value, str)

    def test_contains_required_types(self):
        required = {"offer", "answer", "ice_candidate", "join", "leave", "error"}
        actual = {s.value for s in SignalType}
        assert required.issubset(actual)

    def test_contains_participant_types(self):
        required = {"participant_joined", "participant_left", "existing_participants"}
        actual = {s.value for s in SignalType}
        assert required.issubset(actual)

    def test_contains_lock_types(self):
        required = {"lock_room", "unlock_room", "room_locked", "room_unlocked"}
        actual = {s.value for s in SignalType}
        assert required.issubset(actual)

    def test_contains_chat_type(self):
        actual = {s.value for s in SignalType}
        assert "chat" in actual


class TestServerSettings:
    def test_defaults(self):
        settings = ServerSettings()
        assert settings.host == DEFAULT_HOST
        assert settings.port == DEFAULT_PORT
        assert settings.stun_server == STUN_SERVER_URL
        assert settings.max_participants_per_room == MAX_PARTICIPANTS_PER_ROOM

    def test_override_via_environment(self):
        env = {
            "HOPIN_HOST": "127.0.0.1",
            "HOPIN_PORT": "9090",
            "HOPIN_MAX_PARTICIPANTS_PER_ROOM": "3",
            "HOPIN_STUN_SERVER": "stun:stun.example.test:3478",
        }
        with patch.dict(os.environ, env):
            settings = ServerSettings()
            assert settings.host == "127.0.0.1"
            assert settings.port == 9090
            assert settings.max_participants_per_room == 3
            assert settings.stun_server == "stun:stun.example.test:3478"

    @pytest.mark.parametrize("limit", [MIN_PARTICIPANTS_PER_ROOM, MAX_PARTICIPANTS_PER_ROOM])
    def test_participant_limit_at_the_ends_of_the_range_is_accepted(self, limit):
        assert ServerSettings(max_participants_per_room=limit).max_participants_per_room == limit

    @pytest.mark.parametrize("limit", [MIN_PARTICIPANTS_PER_ROOM - 1, MAX_PARTICIPANTS_PER_ROOM + 1])
    def test_participant_limit_outside_the_range_is_refused(self, limit):
        with pytest.raises(ValidationError):
            ServerSettings(max_participants_per_room=limit)
