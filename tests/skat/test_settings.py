import pytest
from pydantic import ValidationError

from hopin.config.secret_files import StartRefusedError
from hopin.skat.feature import configured_skat_feature

PUBLIC_ORIGIN = "https://hopin.example"


@pytest.fixture
def skat_origin(monkeypatch):
    def configure(value):
        if value is None:
            monkeypatch.delenv("HOPIN_SKAT_ORIGIN", raising=False)
        else:
            monkeypatch.setenv("HOPIN_SKAT_ORIGIN", value)

    return configure


def test_unset_means_no_skat_at_all(skat_origin):
    skat_origin(None)

    assert configured_skat_feature(PUBLIC_ORIGIN) == ()


def test_a_configured_origin_gives_one_feature_that_frames_exactly_that_origin(skat_origin):
    skat_origin("https://skat.example:8443")

    [feature] = configured_skat_feature(PUBLIC_ORIGIN)

    assert feature.frame_sources == ("https://skat.example:8443",)
    assert feature.sign_in_scopes == ()


@pytest.mark.parametrize(
    "public_origin",
    [
        "https://skat.example",
        "https://SKAT.example",
        "https://skat.example:443",
        "https://skat.example.",
        "https://skat.example:443/",
    ],
)
def test_an_origin_equal_to_the_public_origin_refuses_the_start_naming_both_settings(skat_origin, public_origin):
    skat_origin("https://skat.example")

    with pytest.raises(StartRefusedError) as refusal:
        configured_skat_feature(public_origin)

    assert "HOPIN_SKAT_ORIGIN" in str(refusal.value)
    assert "HOPIN_PUBLIC_ORIGIN" in str(refusal.value)


def test_another_port_is_another_origin(skat_origin):
    skat_origin("https://hopin.example:8443")

    assert len(configured_skat_feature(PUBLIC_ORIGIN)) == 1


@pytest.mark.parametrize(
    "value",
    [
        "http://skat.example",
        "https://skat.example/",
        "https://skat.example/t",
        "https://skat.example:443",
        "https://SKAT.example",
        "https://skat.example.",
        "https://user@skat.example",
        "https://skat.example?x=1",
        "https://skat.example#x",
        "https://skät.example",
        "https://",
        "https://:8443",
        "skat.example",
        "",
    ],
)
def test_a_malformed_origin_stops_the_start_without_echoing_it(skat_origin, value):
    skat_origin(value)

    with pytest.raises(ValidationError) as refusal:
        configured_skat_feature(PUBLIC_ORIGIN)

    assert "skat.example" not in str(refusal.value)
