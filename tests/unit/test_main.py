from unittest.mock import patch

from hopin.config.constants import UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.main import main


class TestMain:
    @patch("hopin.main.uvicorn.run")
    def test_starts_server_with_configured_host_and_port(self, mock_run):
        main()
        mock_run.assert_called_once()
        kwargs = mock_run.call_args[1]
        assert "host" in kwargs
        assert "port" in kwargs

    @patch("hopin.main.uvicorn.run")
    def test_serves_websockets_with_the_sans_io_implementation(self, mock_run):
        main()
        assert mock_run.call_args[1]["ws"] == UVICORN_WEBSOCKET_IMPLEMENTATION
