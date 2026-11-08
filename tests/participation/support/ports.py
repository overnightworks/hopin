import time


async def groups_never_read(connection):
    """A groups read of a participation whose adders are not configured: nothing asks it."""
    del connection
    return None


def unconfigured_ports():
    """The ports a participation takes beside its rooms and scheduler, for a test where nobody may add."""
    return {"wall_clock": time.time, "adders": {}, "groups_of": groups_never_read}
