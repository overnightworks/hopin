import pytest

from openstream.errors import (
    OpenStreamError,
    RoomAlreadyExistsError,
    RoomFullError,
    RoomNotFoundError,
    StreamerAlreadyConnectedError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        errors = [RoomNotFoundError, RoomFullError, RoomAlreadyExistsError, StreamerAlreadyConnectedError]
        for error_cls in errors:
            assert issubclass(error_cls, OpenStreamError)

    def test_base_inherits_from_exception(self):
        assert issubclass(OpenStreamError, Exception)


class TestRoomNotFoundError:
    def test_stores_room_id(self):
        error = RoomNotFoundError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        error = RoomNotFoundError("abc123")
        assert "abc123" in str(error)


class TestRoomFullError:
    def test_stores_room_id(self):
        error = RoomFullError("abc123")
        assert error.room_id == "abc123"

    def test_is_catchable_as_base(self):
        with pytest.raises(OpenStreamError):
            raise RoomFullError("abc123")


class TestRoomAlreadyExistsError:
    def test_stores_room_id(self):
        error = RoomAlreadyExistsError("abc123")
        assert error.room_id == "abc123"


class TestStreamerAlreadyConnectedError:
    def test_stores_room_id(self):
        error = StreamerAlreadyConnectedError("abc123")
        assert error.room_id == "abc123"
