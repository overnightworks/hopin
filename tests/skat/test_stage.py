from hopin.skat.stage import Stages

TABLE = "a" * 32
SOURCE = f"https://skat.example/t/{TABLE}"


def test_the_calls_end_clears_its_table_and_nothing_of_another_call():
    stages = Stages()
    stages.attach("ended", SOURCE, TABLE, "opener")
    stages.attach("other", SOURCE, TABLE, "opener")

    stages.end("ended")

    assert (stages.state_of("ended").attachment, stages.state_of("other").attachment is None) == (None, False)


def test_every_change_of_any_room_is_given_a_higher_revision():
    stages = Stages()

    stages.attach("one", SOURCE, TABLE, "opener")
    stages.attach("two", SOURCE, TABLE, "opener")
    stages.detach("one")
    stages.forget_opener("opener")

    assert stages.state_of("one").revision < stages.state_of("two").revision
    assert stages.state_of("two").attachment.opener is None
