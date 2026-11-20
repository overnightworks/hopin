import pytest

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")


def test_refuses_a_malformed_skat_origin_in_one_line_naming_it_and_never_its_value(serve_refusal):
    serve_refusal("HOPIN_SKAT_ORIGIN", "http://sentinel-skat.test", "HOPIN_SKAT_ORIGIN is malformed")
