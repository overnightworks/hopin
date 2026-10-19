import pytest

from hopin.config.constants import ErrorReason, ResumeRefusalCause
from hopin.errors import (
    HopInError,
    InvalidMessageError,
    NotHostError,
    ResumeRefusedError,
    RoomFullError,
    RoomLockedError,
    RoomNotFoundError,
)


class TestErrorHierarchy:
    def test_all_errors_inherit_from_base(self):
        errors = [
            RoomNotFoundError,
            RoomFullError,
            RoomLockedError,
            NotHostError,
            InvalidMessageError,
            ResumeRefusedError,
        ]
        for error_cls in errors:
            assert issubclass(error_cls, HopInError)

    def test_base_inherits_from_exception(self):
        assert issubclass(HopInError, Exception)


class TestErrorReason:
    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (RoomNotFoundError("abc123"), ErrorReason.NOT_FOUND),
            (RoomLockedError("abc123"), ErrorReason.LOCKED),
            (RoomFullError("abc123", 6), ErrorReason.FULL),
            (NotHostError("abc123"), ErrorReason.NOT_HOST),
            (InvalidMessageError("bad frame"), ErrorReason.INVALID_MESSAGE),
            (ResumeRefusedError(ResumeRefusalCause.MALFORMED), ErrorReason.RESUME_REFUSED),
        ],
    )
    def test_reason_error_type_carries_its_reason(self, error, reason):
        assert error.reason is reason


class TestRoomNotFoundError:
    def test_stores_room_id(self):
        error = RoomNotFoundError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        error = RoomNotFoundError("abc123")
        assert "abc123" in str(error)


class TestRoomFullError:
    def test_stores_room_id(self):
        error = RoomFullError("abc123", 6)
        assert error.room_id == "abc123"

    def test_is_catchable_as_base(self):
        with pytest.raises(HopInError):
            raise RoomFullError("abc123", 6)

    def test_carries_the_capacity_into_its_frame_fields(self):
        assert RoomFullError("abc123", 6).frame_fields() == {"capacity": 6}


class TestFrameFields:
    def test_error_without_extra_fields_adds_nothing_to_its_frame(self):
        assert RoomLockedError("abc123").frame_fields() == {}


class TestRoomLockedError:
    def test_stores_room_id(self):
        error = RoomLockedError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        assert "abc123" in str(RoomLockedError("abc123"))


class TestInvalidMessageError:
    def test_message_is_the_detail(self):
        assert str(InvalidMessageError("Invalid JSON")) == "Invalid JSON"


class TestNotHostError:
    def test_stores_room_id(self):
        error = NotHostError("abc123")
        assert error.room_id == "abc123"

    def test_message_contains_room_id(self):
        assert "abc123" in str(NotHostError("abc123"))


class TestResumeRefusedError:
    def test_carries_the_cause_for_the_log_and_a_message_that_names_none(self):
        error = ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)

        assert error.cause is ResumeRefusalCause.CREDENTIAL_MISMATCH
        assert str(error) == "Resume refused"
