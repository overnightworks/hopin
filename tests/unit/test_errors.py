import pytest

from hopin.config.constants import ErrorReason, ResumeRefusalCause
from hopin.errors import (
    InvalidMessageError,
    NotHostError,
    ResumeRefusedError,
    RoomFullError,
    RoomLockedError,
    RoomNotFoundError,
)


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
def test_reason_error_type_carries_its_reason(error, reason):
    assert error.reason is reason


@pytest.mark.parametrize(
    ("error", "fields"),
    [
        (RoomFullError("abc123", 6), {"capacity": 6}),
        (RoomLockedError("abc123"), {}),
    ],
)
def test_frame_fields_carry_only_what_the_error_adds(error, fields):
    assert error.frame_fields() == fields


def test_resume_refusal_carries_the_cause_for_the_log_and_a_message_that_names_none():
    error = ResumeRefusedError(ResumeRefusalCause.CREDENTIAL_MISMATCH)

    assert error.cause is ResumeRefusalCause.CREDENTIAL_MISMATCH
    assert str(error) == "Resume refused"
