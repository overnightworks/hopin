import pytest

from hopin.stage.settings import linked_context
from tests.stage.support.tables import ORIGIN, SKAT
from tests.stage.support.tables import TABLE_ID as KEY

CHESS = SKAT.model_copy(
    update={"name": "chess", "origin": "https://chess.example", "frame_path": "/play/{context}/live"}
)
APPS = (SKAT, CHESS)


@pytest.mark.parametrize(
    "link",
    [
        f"{ORIGIN}/t/{KEY}",
        f"{ORIGIN}/t/{KEY}?mode=host#seat",
        f"https://SKAT.example/t/{KEY}",
        f"https://skat.example:443/t/{KEY}",
        f"https://skat.example./t/{KEY}",
    ],
)
def test_a_link_to_a_table_of_the_skat_entry_becomes_its_key_and_the_call_mode_address_of_today(link):
    linked = linked_context(APPS, link)

    assert (linked.app, linked.context, linked.source) == (SKAT, KEY, f"{ORIGIN}/t/{KEY}?mode=call")


def test_a_link_is_matched_against_the_path_of_the_entry_of_its_origin():
    linked = linked_context(APPS, "https://chess.example/play/game-7_A/live?x=1")

    assert (linked.app.name, linked.context) == ("chess", "game-7_A")
    assert linked.source == "https://chess.example/play/game-7_A/live?mode=call"


@pytest.mark.parametrize("key", ["a", "A_-9", "a" * 64])
def test_a_context_key_within_the_one_bound_is_accepted_whatever_the_app(key):
    assert linked_context(APPS, f"{ORIGIN}/t/{key}").context == key


@pytest.mark.parametrize(
    "link",
    [
        f"https://other.example/t/{KEY}",
        f"http://skat.example/t/{KEY}",
        f"https://skat.example:8443/t/{KEY}",
        f"https://user@skat.example/t/{KEY}",
        f"{ORIGIN}/t/",
        f"{ORIGIN}/t/{'a' * 65}",
        f"{ORIGIN}/t/a.b",
        f"{ORIGIN}/t/a%20b",
        f"{ORIGIN}/t/{KEY}/extra",
        f"{ORIGIN}/x/{KEY}",
        f"https://chess.example/t/{KEY}",
        f"https://chess.example/play/{KEY}",
        f"{ORIGIN}/play/{KEY}/live",
        f"javascript:alert(1)//{ORIGIN}/t/{KEY}",
        f"https://skat.example:99999/t/{KEY}",
        f"https:///t/{KEY}",
        f"ftp://skat.example/t/{KEY}",
        "",
        "not a link",
        "https://[broken/t/" + KEY,
    ],
)
def test_any_other_link_is_no_table_of_a_registered_app(link):
    assert linked_context(APPS, link) is None
