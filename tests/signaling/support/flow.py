from logline import WEBSOCKET_EVENT

POLICY_VIOLATION_CODE = 1008


def events_named(logs, event):
    return [entry for entry in logs if entry["event"] == event]


def lines_of_the_handler(logs):
    """The lines without the `sub` logline writes into the socket's session line: the admitted person, by contract."""
    return [
        {key: value for key, value in entry.items() if key != "sub"} if entry["event"] == WEBSOCKET_EVENT else entry
        for entry in logs
    ]


def expires_at(stores, member):
    stored = stores.sessions.load(member.session.id)
    assert stored is not None
    return stored.expires_at


def number_frames_after_the_first(websocket):
    """Make send_json number each frame after the socket's first, as the browser does."""
    send_unnumbered = websocket.send_json
    sent = 0

    def send_json(data):
        nonlocal sent
        send_unnumbered(data if sent == 0 else {**data, "seq": sent})
        sent += 1

    websocket.send_json = send_json
    return websocket
