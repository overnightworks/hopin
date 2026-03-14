from unittest.mock import patch

from hopin.main import main


class TestMain:
    @patch("hopin.main.web.run_app")
    def test_starts_server_with_configured_host_and_port(self, mock_run_app):
        main()
        mock_run_app.assert_called_once()
        kwargs = mock_run_app.call_args[1]
        assert "host" in kwargs
        assert "port" in kwargs
