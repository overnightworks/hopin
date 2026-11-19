import pytest

from hopin.main import main
from tests.support.game_environment import GAME_ENVIRONMENT

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")


class TestServeRefusesTheGameSettings:
    @pytest.mark.parametrize(
        ("setting", "value", "expected_line"),
        [
            pytest.param(
                "HOPIN_AGENT__API_GROUP", None, "HOPIN_AGENT__API_GROUP is missing", id="missing_agent_api_group"
            ),
            pytest.param(
                "HOPIN_AGENT__GATEWAY__ORIGIN",
                "sentinel-gateway",
                "HOPIN_AGENT__GATEWAY__ORIGIN is malformed",
                id="malformed_agent_gateway_origin",
            ),
        ],
    )
    def test_refuses_a_missing_or_malformed_agent_setting_in_one_line_naming_it_and_never_its_value(
        self, sign_in_settings, monkeypatch, capsys, setting, value, expected_line
    ):
        for name, environment_value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, environment_value)
        if value is None:
            monkeypatch.delenv(setting)
        else:
            monkeypatch.setenv(setting, value)

        with pytest.raises(SystemExit) as refusal:
            main(["serve"])

        assert refusal.value.code == 1
        lines = capsys.readouterr().err.splitlines()
        assert len(lines) == 1
        assert expected_line in lines[0]
        assert "sentinel" not in lines[0]

    def test_refuses_with_no_agent_setting_at_all_naming_the_family(self, sign_in_settings, monkeypatch, capsys):
        for name, environment_value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, environment_value)
        for name in GAME_ENVIRONMENT:
            monkeypatch.delenv(name)

        with pytest.raises(SystemExit) as refusal:
            main(["serve"])

        assert refusal.value.code == 1
        assert capsys.readouterr().err == "HOPIN_AGENT__* is missing\n"
