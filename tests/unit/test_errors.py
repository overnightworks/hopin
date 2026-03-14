import pytest

from openstream.errors import (
    NotHostError,
    OpenStreamError,
    RoomFullError,
    RoomLockedError,
    RoomNotFoundError,
    WrongPasswordError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        errors = [RoomNotFoundError, RoomFullError, RoomLockedError, WrongPasswordError, NotHostError]
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


class TestRoomLockedError:
    def test_stores_room_id(self):
        error = RoomLockedError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        assert "abc123" in str(RoomLockedError("abc123"))


class TestWrongPasswordError:
    def test_stores_room_id(self):
        error = WrongPasswordError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        assert "abc123" in str(WrongPasswordError("abc123"))


class TestNotHostError:
    def test_stores_room_id(self):
        error = NotHostError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        assert "abc123" in str(NotHostError("abc123"))
