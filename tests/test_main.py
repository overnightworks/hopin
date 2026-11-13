from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from hopin.config.constants import POLICY_VIOLATION_CLOSE_CODE, UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.secret_files import StartRefusedError
from hopin.config.settings import ServerSettings
from hopin.main import build_application, main
from hopin.participation.vocabulary import HEARING_PATH
from tests.support.dialed_call import METADATA_PATH
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.usefixtures("fixture_build_directory")


class TestMain:
    @patch("hopin.main.uvicorn.run")
    def test_starts_server_with_configured_host_and_port(self, mock_run, sign_in_settings, monkeypatch):
        sign_in = sign_in_settings(host="127.0.0.1", port=9090)
        for name, value in sign_in.environment.items():
            monkeypatch.setenv(name, value)
        main(["serve"])
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert kwargs["host"] == "127.0.0.1"
        assert kwargs["port"] == 9090

    @patch("hopin.main.uvicorn.run")
    def test_serves_websockets_with_the_sans_io_implementation(self, mock_run, sign_in_settings, monkeypatch):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        main(["serve"])
        assert mock_run.call_args[1]["ws"] == UVICORN_WEBSOCKET_IMPLEMENTATION

    @patch("hopin.main.uvicorn.run")
    def test_writes_no_access_line_and_logs_only_warnings(self, mock_run, sign_in_settings, monkeypatch):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        main(["serve"])
        assert mock_run.call_args[1]["access_log"] is False
        assert mock_run.call_args[1]["log_level"] == UVICORN_LOG_LEVEL

    @patch("hopin.main.uvicorn.run")
    def test_leaves_the_client_address_to_the_trusted_proxy_setting(self, mock_run, sign_in_settings, monkeypatch):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        main(["serve"])
        assert mock_run.call_args[1]["proxy_headers"] is False

    def test_serves_participation_at_the_mcp_endpoint_for_agents_to_dial_in(self, sign_in_settings, monkeypatch):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        served = TestClient(build_application(ServerSettings()), base_url=TEST_PUBLIC_ORIGIN)

        assert served.get(METADATA_PATH).status_code == 200
        assert served.post("/mcp").status_code == 401

    @pytest.mark.parametrize(
        ("hearing", "route_exists"), [("true", True), ("false", False)], ids=["hearing_on", "hearing_off"]
    )
    def test_serves_the_hearing_route_only_while_hearing_is_on(
        self, sign_in_settings, monkeypatch, hearing, route_exists
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("HOPIN_SPEECH__ORIGIN", "http://speech.test:8090")
        monkeypatch.setenv("HOPIN_SPEECH__AUDIENCE_SCOPE", "scenario-audience")
        monkeypatch.setenv("HOPIN_SPEECH__HEARING", hearing)
        served = TestClient(build_application(ServerSettings()), base_url=TEST_PUBLIC_ORIGIN)

        with pytest.raises(WebSocketDisconnect) as refused, served.websocket_connect(f"{HEARING_PATH}/a-seat"):
            pass

        assert (refused.value.code == POLICY_VIOLATION_CLOSE_CODE) is route_exists

    @pytest.mark.parametrize("setting", ["HOPIN_SPEECH__HEARING", "HOPIN_SPEECH__SPEAKING"])
    def test_refuses_to_start_when_hearing_or_speaking_is_on_without_a_speech_service(
        self, sign_in_settings, monkeypatch, setting
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv(setting, "true")

        with pytest.raises(ValidationError):
            build_application(ServerSettings())

    @patch("hopin.main.uvicorn.run")
    def test_refuses_to_start_without_the_page_build_and_names_the_build_command(
        self, mock_run, sign_in_settings, monkeypatch, tmp_path
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setattr("hopin.main.BUILD_DIRECTORY", tmp_path / "build")

        with pytest.raises(StartRefusedError, match="make frontend"):
            main(["serve"])

        mock_run.assert_not_called()

    @pytest.mark.parametrize(
        ("sources", "starts"),
        [
            pytest.param('["https://figures.example.test/personas/"]', True, id="an allowed prefix starts"),
            pytest.param('["https://figures.example.test/personas"]', False, id="a malformed prefix refuses"),
        ],
    )
    @patch("hopin.main.uvicorn.run")
    def test_starts_only_with_well_formed_figure_sources(
        self, mock_run, sources, starts, sign_in_settings, monkeypatch
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        monkeypatch.setenv("HOPIN_FIGURE_SOURCES", sources)

        if starts:
            main(["serve"])
        else:
            with pytest.raises(ValidationError, match="figure_sources"):
                main(["serve"])

        assert mock_run.called is starts

    @pytest.mark.parametrize("arguments", [[], ["frobnicate"]])
    @patch("hopin.main.uvicorn.run")
    def test_refuses_without_the_serve_command_and_starts_nothing(self, mock_run, arguments, capsys):
        with pytest.raises(SystemExit) as refusal:
            main(arguments)
        assert refusal.value.code != 0
        assert "serve" in capsys.readouterr().err
        mock_run.assert_not_called()
