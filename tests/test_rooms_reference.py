from hopin.rooms.reference import room_reference


def test_a_room_has_one_reference_that_is_not_its_id():
    reference = room_reference("abcd1234")

    assert (reference == room_reference("abcd1234"), "abcd1234" in reference) == (True, False)


def test_two_rooms_have_two_references():
    assert room_reference("abcd1234") != room_reference("abcd1235")
