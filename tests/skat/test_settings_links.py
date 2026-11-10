import pytest

from hopin.skat.settings import table_source

ORIGIN = "https://skat.example"
TABLE_ID = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    "link",
    [
        f"{ORIGIN}/t/{TABLE_ID}",
        f"{ORIGIN}/t/{TABLE_ID}?mode=host#seat",
        f"https://SKAT.example/t/{TABLE_ID}",
        f"https://skat.example:443/t/{TABLE_ID}",
        f"https://skat.example./t/{TABLE_ID}",
    ],
)
def test_a_link_to_a_table_of_the_origin_becomes_its_id_and_the_call_mode_address(link):
    address = table_source(ORIGIN, link)

    assert (address.table, address.source) == (TABLE_ID, f"{ORIGIN}/t/{TABLE_ID}?mode=call")


@pytest.mark.parametrize(
    "link",
    [
        f"https://other.example/t/{TABLE_ID}",
        f"http://skat.example/t/{TABLE_ID}",
        f"https://skat.example:8443/t/{TABLE_ID}",
        f"https://user@skat.example/t/{TABLE_ID}",
        f"{ORIGIN}/t/{TABLE_ID[:31]}",
        f"{ORIGIN}/t/{TABLE_ID}0",
        f"{ORIGIN}/t/{TABLE_ID.upper()}",
        f"{ORIGIN}/t/{TABLE_ID}/extra",
        f"{ORIGIN}/x/{TABLE_ID}",
        f"javascript:alert(1)//{ORIGIN}/t/{TABLE_ID}",
        f"https://skat.example:99999/t/{TABLE_ID}",
        f"https:///t/{TABLE_ID}",
        f"ftp://skat.example/t/{TABLE_ID}",
        "",
        "not a link",
        "https://[broken/t/" + TABLE_ID,
    ],
)
def test_any_other_link_is_no_table_of_the_origin(link):
    assert table_source(ORIGIN, link) is None
