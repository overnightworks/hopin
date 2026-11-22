import traceback
from urllib.parse import urlunsplit

import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from hopin.participation.settings import ParticipationSettings

SOURCES_VARIABLE = "HOPIN_FIGURE_SOURCES"
ALLOWED_PREFIX = "https://figures.example.test/personas/releases/download/"


def sources_with(monkeypatch, value):
    monkeypatch.setenv(SOURCES_VARIABLE, value)
    return ParticipationSettings().figure_sources


class TestTheFigureSourcesSetting:
    def test_no_source_is_allowed_when_it_is_unset(self, monkeypatch):
        monkeypatch.delenv(SOURCES_VARIABLE, raising=False)

        assert ParticipationSettings().figure_sources == ()

    @pytest.mark.parametrize(
        "prefix",
        [
            pytest.param(ALLOWED_PREFIX, id="a release download path"),
            pytest.param("https://figures.example.test/", id="the whole host"),
            pytest.param("https://figures.example.test/a-b.c_d~e/", id="every allowed character"),
        ],
    )
    def test_a_well_formed_prefix_is_kept_with_its_host_and_path(self, monkeypatch, prefix):
        [source] = sources_with(monkeypatch, f'["{prefix}"]')

        assert urlunsplit(("https", source.host, source.path, "", "")) == prefix

    @pytest.mark.parametrize(
        "prefix",
        [
            pytest.param("https://figures.example.test/personas", id="no closing slash"),
            pytest.param("http://figures.example.test/personas/", id="http"),
            pytest.param("https://figures.example.test:443/personas/", id="a port"),
            pytest.param("https://user@figures.example.test/personas/", id="a user"),
            pytest.param("https://figures.example.test/personas/?a=b", id="a query"),
            pytest.param("https://figures.example.test/personas/#top", id="a fragment"),
            pytest.param("https://Figures.example.test/personas/", id="an uppercase host"),
            pytest.param("https://figures.example.test/person%61s/", id="a percent"),
            pytest.param("https://figures.example.test/personas\\\\x/", id="a backslash"),
            pytest.param("https://figures.example.test/personas/../", id="a dot-dot segment"),
            pytest.param("https://figures.example.test/personas//", id="an empty segment"),
            pytest.param("https://figures.example.test/person+as/", id="a disallowed character"),
            pytest.param("https://figures.example.test", id="no path"),
            pytest.param("figures.example.test/personas/", id="no scheme"),
            pytest.param(" https://figures.example.test/personas/", id="a leading space"),
            pytest.param("https://figures.example.test/personas/\\t", id="a trailing tab"),
            pytest.param("https://figures.example.test/pers\\nonas/", id="a newline inside"),
            pytest.param("https://figures.example.test/personas/\\u0007", id="a non-printable character"),
        ],
    )
    def test_a_malformed_prefix_stops_the_start_naming_only_the_setting(self, monkeypatch, prefix):
        with pytest.raises(ValidationError) as refusal:
            sources_with(monkeypatch, f'["{ALLOWED_PREFIX}", "{prefix}"]')

        assert "figure_sources" in str(refusal.value)
        assert prefix not in str(refusal.value)
        assert "input_value" not in str(refusal.value)

    def test_a_prefix_that_is_no_string_stops_the_start(self, monkeypatch):
        with pytest.raises(ValidationError) as refusal:
            sources_with(monkeypatch, "[7]")

        assert "figure_sources" in str(refusal.value)

    def test_a_value_that_is_no_json_list_stops_the_start_naming_the_setting_and_echoing_nothing(self, monkeypatch):
        with pytest.raises(SettingsError) as refusal:
            sources_with(monkeypatch, f"[{ALLOWED_PREFIX}")

        assert "figure_sources" in str(refusal.value)
        assert ALLOWED_PREFIX not in "".join(traceback.format_exception(refusal.value))
