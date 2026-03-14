import pytest

from openstream.errors import (
    OpenStreamError,
    RoomFullError,
    RoomNotFoundError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        errors = [RoomNotFoundError, RoomFullError]
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
