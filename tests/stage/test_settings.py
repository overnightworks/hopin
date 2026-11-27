import json

import pytest

from hopin.config.secret_files import StartRefusedError
from hopin.stage.feature import configured_stage_feature
from tests.stage.support.tables import entry

PUBLIC_ORIGIN = "https://hopin.example"


@pytest.fixture
def stage_apps(monkeypatch):
    def configure(*entries):
        monkeypatch.setenv("HOPIN_STAGE_APPS", json.dumps(entries))

    monkeypatch.delenv("HOPIN_STAGE_APPS", raising=False)
    return configure


def test_unset_means_no_stage_at_all(stage_apps):
    """ST-07."""
    assert configured_stage_feature(PUBLIC_ORIGIN) == ()


def test_an_empty_list_means_no_stage_at_all(stage_apps):
    """ST-07."""
    stage_apps()

    assert configured_stage_feature(PUBLIC_ORIGIN) == ()


def test_each_entry_gives_an_app_and_the_feature_frames_exactly_their_origins(stage_apps):
    """ST-07."""
    stage_apps(entry(origin="https://skat.example:8443"), entry(name="chess", origin="https://chess.example"))

    [feature] = configured_stage_feature(PUBLIC_ORIGIN)

    assert [app.name for app in feature.apps] == ["skat", "chess"]
    assert feature.frame_sources == ("https://skat.example:8443", "https://chess.example")
    assert feature.sign_in_scopes == ()


def test_a_title_has_no_length_limit(stage_apps):
    stage_apps(entry(title="T" * 200))

    [feature] = configured_stage_feature(PUBLIC_ORIGIN)

    assert [len(app.title) for app in feature.apps] == [200]


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
def test_an_origin_equal_to_the_public_origin_refuses_the_start_naming_both_settings(stage_apps, public_origin):
    """ST-07."""
    stage_apps(entry(name="chess", origin="https://chess.example"), entry())

    with pytest.raises(StartRefusedError) as refusal:
        configured_stage_feature(public_origin)

    assert "HOPIN_STAGE_APPS" in str(refusal.value)
    assert "HOPIN_PUBLIC_ORIGIN" in str(refusal.value)


def test_another_port_is_another_origin(stage_apps):
    """ST-07."""
    stage_apps(entry(origin="https://hopin.example:8443"))

    assert len(configured_stage_feature(PUBLIC_ORIGIN)) == 1
