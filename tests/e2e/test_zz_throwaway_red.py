import pytest

pytestmark = pytest.mark.e2e


def test_throwaway_red() -> None:
    raise AssertionError
