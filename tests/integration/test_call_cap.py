import pytest
from starlette.websockets import WebSocketDisconnect

from hopin.config.constants import MAX_LIVE_SEATS_PER_ACCOUNT, ErrorReason, SignalType
from tests.integration.signaling_calls import (
    ABNORMAL_CLOSE_CODE,
    POLICY_VIOLATION_CODE,
    events_named,
    join_room,
    open_room,
    resume_frame,
)

pytestmark = pytest.mark.integration


def open_idle_sockets(connect, account, count):
    return [connect(account) for _ in range(count)]


def assert_refused_for_too_many_calls(websocket):
    refusal = websocket.receive_json()
    with pytest.raises(WebSocketDisconnect) as closed:
        websocket.receive_json()
    assert (refusal, closed.value.code) == (
        {
            "type": SignalType.ERROR.value,
            "reason": ErrorReason.TOO_MANY_CALLS.value,
            "message": refusal["message"],
            "limit": MAX_LIVE_SEATS_PER_ACCOUNT,
        },
        POLICY_VIOLATION_CODE,
    )


def assert_admitted(websocket):
    websocket.send_json({"type": SignalType.JOIN.value})
    assert websocket.receive_json()["type"] == SignalType.ROOM_CREATED.value


def test_socket_beyond_the_cap_gets_the_refusal_frame_and_a_policy_close(connect):
    """#238 line 1: a socket past the cap is refused with too_many_calls and closed."""
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT)

    assert_refused_for_too_many_calls(connect("busy"))


def test_other_account_is_admitted_while_one_account_is_at_the_cap(connect):
    """#238 line 1: the cap is per account, so another account still gets in."""
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT)

    assert_admitted(connect("other"))


def test_refused_socket_opens_no_seat_so_closing_a_live_one_lets_a_new_socket_in(connect):
    """#238 line 1: a refusal holds no seat, and closing a live call frees room for a new one."""
    live = open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT)
    assert_refused_for_too_many_calls(connect("busy"))
    live[0].close()

    assert_admitted(connect("busy"))


def test_parked_seat_does_not_count_toward_the_cap(connect):
    """#238 line 1: only live calls count, a seat parked for resume does not."""
    host_ws, room_id = open_room(connect, "host1")
    guest_ws, _ = join_room(connect, "busy", room_id)
    host_ws.receive_json()
    guest_ws.close(code=ABNORMAL_CLOSE_CODE)
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT - 1)

    assert_admitted(connect("busy"))


def test_resume_is_admitted_when_the_old_socket_is_the_cap_overlap(connect):
    """#238 line 1: a takeover that replaces a live socket does not need a free seat."""
    host_ws, room_id = open_room(connect, "host1")
    _, seat = join_room(connect, "busy", room_id)
    host_ws.receive_json()
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT - 2)
    resumed_ws = connect("busy")

    resumed_ws.send_json(resume_frame(seat))

    assert resumed_ws.receive_json()["type"] == SignalType.RESUMED.value


def test_resume_at_the_cap_ends_with_the_refusal(connect):
    """#238 line 1: a resume refused at the cap ends with the too_many_calls refusal."""
    host_ws, room_id = open_room(connect, "host1")
    _, seat = join_room(connect, "busy", room_id)
    host_ws.receive_json()
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT - 1)
    resumed_ws = connect("busy")

    resumed_ws.send_json(resume_frame(seat))

    assert_refused_for_too_many_calls(resumed_ws)


def test_refusal_log_carries_the_cap_only(connect, member_named, logs):
    """#238 line 1: the refusal is logged with the limit and no account or session detail."""
    open_idle_sockets(connect, "busy", MAX_LIVE_SEATS_PER_ACCOUNT)
    assert_refused_for_too_many_calls(connect("busy"))

    member = member_named("busy")
    secrets_in_logs = [member.account.username, member.account.id, member.session.id]
    assert [set(entry) for entry in events_named(logs, "too_many_calls")] == [{"event", "limit", "log_level"}]
    assert [secret for secret in secrets_in_logs if secret in str(logs)] == []
