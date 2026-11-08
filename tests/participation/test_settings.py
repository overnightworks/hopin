import pytest
from pydantic import ValidationError
from pydantic_settings import SettingsError

from hopin.participation.settings import ParticipationSettings

ADDERS_VARIABLE = "HOPIN_AGENT_ADDERS"


def adders_with(monkeypatch, value):
    monkeypatch.setenv(ADDERS_VARIABLE, value)
    return ParticipationSettings().agent_adders


class TestTheAddersSetting:
    def test_nobody_adds_when_it_is_unset(self, monkeypatch):
        monkeypatch.delenv(ADDERS_VARIABLE, raising=False)

        assert ParticipationSettings().agent_adders == {}

    def test_it_maps_an_owner_to_the_full_path_of_the_group(self, monkeypatch):
        assert adders_with(monkeypatch, '{"owner": "/apps/hopin/agent-adders"}') == {
            "owner": "/apps/hopin/agent-adders"
        }

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("apps", id="no leading slash"),
            pytest.param("/", id="no segment"),
            pytest.param("/apps//adders", id="an empty segment"),
            pytest.param("/apps/ adders", id="whitespace around a segment"),
            pytest.param("/apps/adders\\u0007", id="a control character"),
        ],
    )
    def test_a_path_that_is_no_group_path_stops_the_start_without_echoing_it(self, monkeypatch, path):
        with pytest.raises(ValidationError) as refusal:
            adders_with(monkeypatch, f'{{"owner": "{path}"}}')

        assert "input_value" not in str(refusal.value)

    def test_a_value_that_is_no_json_object_stops_the_start(self, monkeypatch):
        with pytest.raises(SettingsError):
            adders_with(monkeypatch, "not json")
