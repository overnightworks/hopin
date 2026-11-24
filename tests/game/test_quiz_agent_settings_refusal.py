import pytest

from hopin.main import main
from tests.support.game_environment import GAME_ENVIRONMENT

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")


class TestServeRefusesTheGameSettings:
    @pytest.mark.parametrize(
        ("setting", "value", "expected_line"),
        [
            pytest.param("REALM_AI_GROUP", None, "REALM_AI_GROUP is missing", id="missing_agent_api_group"),
            pytest.param(
                "HOPIN_AGENT__GATEWAY__ORIGIN",
                "sentinel-gateway",
                "HOPIN_AGENT__GATEWAY__ORIGIN is malformed",
                id="malformed_agent_gateway_origin",
            ),
        ],
    )
    def test_refuses_a_missing_or_malformed_agent_setting_in_one_line_naming_it_and_never_its_value(
        self, serve_refusal, setting, value, expected_line
    ):
        serve_refusal(setting, value, expected_line)

    @pytest.mark.parametrize(
        "leftover",
        [
            pytest.param({}, id="nothing_set"),
            pytest.param({"HOPIN_AGENT__API_GROUP": "sentinel-group"}, id="only_the_old_group_key_set"),
        ],
    )
    def test_refuses_with_no_agent_setting_at_all_naming_the_family_and_the_group_key(
        self, sign_in_settings, monkeypatch, capsys, leftover
    ):
        for name, environment_value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, environment_value)
        for name in GAME_ENVIRONMENT:
            monkeypatch.delenv(name)
        for name, environment_value in leftover.items():
            monkeypatch.setenv(name, environment_value)

        with pytest.raises(SystemExit) as refusal:
            main(["serve"])

        assert refusal.value.code == 1
        assert capsys.readouterr().err == "HOPIN_AGENT__* is missing\nREALM_AI_GROUP is missing\n"
