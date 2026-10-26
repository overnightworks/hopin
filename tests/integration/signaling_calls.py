from hopin.config.constants import SignalType

POLICY_VIOLATION_CODE = 1008
ABNORMAL_CLOSE_CODE = 4999


def events_named(logs, event):
    return [entry for entry in logs if entry["event"] == event]


def open_room(connect, account):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value})
    return ws, ws.receive_json()["room_id"]


def join_room(connect, account, room_id):
    ws = connect(account)
    ws.send_json({"type": SignalType.JOIN.value, "room_id": room_id})
    return ws, ws.receive_json()


def resume_frame(seat, **overrides):
    return {
        "type": SignalType.RESUME.value,
        "participant_id": seat["participant_id"],
        "token": seat["resume_token"],
        "last_seq": seat["seq"],
        "sent_floor": 0,
        **overrides,
    }
