from hopin.stage.stage import RoomState

TABLE = "a" * 32
SOURCE = f"https://skat.example/t/{TABLE}"


def test_every_change_of_a_room_gives_it_a_higher_revision():
    attached = RoomState().attached(table=TABLE, source=SOURCE, opener="opener")
    without_opener = attached.without_opener("opener")
    detached = without_opener.detached()

    assert attached.revision < without_opener.revision < detached.revision
    assert (without_opener.attachment.opener, detached.attachment) == (None, None)


def test_a_seat_that_opened_nothing_leaves_the_room_unchanged():
    attached = RoomState().attached(table=TABLE, source=SOURCE, opener="opener")

    assert attached.without_opener("guest") == attached
