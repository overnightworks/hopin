from unittest.mock import patch

import pytest

from hopin.config.constants import UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.main import main


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

    @pytest.mark.parametrize("arguments", [[], ["frobnicate"]])
    @patch("hopin.main.uvicorn.run")
    def test_refuses_without_the_serve_command_and_starts_nothing(self, mock_run, arguments, capsys):
        with pytest.raises(SystemExit) as refusal:
            main(arguments)
        assert refusal.value.code != 0
        assert "serve" in capsys.readouterr().err
        mock_run.assert_not_called()
