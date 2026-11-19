import socket

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from hopin.config.constants import POLICY_VIOLATION_CLOSE_CODE, UVICORN_LOG_LEVEL, UVICORN_WEBSOCKET_IMPLEMENTATION
from hopin.config.secret_files import StartRefusedError
from hopin.config.settings import ServerSettings
from hopin.main import UvicornOptions, build_application, main, run_uvicorn, serve, uvicorn_options
from hopin.participation.vocabulary import HEARING_PATH
from tests.support.dialed_call import METADATA_PATH
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_settings import TEST_PUBLIC_ORIGIN

pytestmark = pytest.mark.usefixtures("game_environment", "logging_restored")


@pytest.fixture
def serve_over_test_stores(sign_in_settings, monkeypatch):
    """Serves the fixture build over in-thread stores and returns the options uvicorn would run with."""
    for name, value in sign_in_settings().environment.items():
        monkeypatch.setenv(name, value)

    def serve_and_record() -> UvicornOptions:
        started: list[UvicornOptions] = []
        serve(
            ServerSettings(),
            fixture_page_build(),
            in_thread_stores(),
            run=lambda _application, options: started.append(options),
        )
        (options,) = started
        return options

    return serve_and_record


class TestMain:
    def test_starts_server_with_configured_host_and_port(self, monkeypatch, serve_over_test_stores):
        monkeypatch.setenv("HOPIN_HOST", "127.0.0.1")
        monkeypatch.setenv("HOPIN_PORT", "9090")
        options = serve_over_test_stores()
        assert options["host"] == "127.0.0.1"
        assert options["port"] == 9090

    def test_serves_websockets_with_the_sans_io_implementation(self, serve_over_test_stores):
        assert serve_over_test_stores()["ws"] == UVICORN_WEBSOCKET_IMPLEMENTATION

    def test_writes_no_access_line_and_logs_only_warnings(self, serve_over_test_stores):
        options = serve_over_test_stores()
        assert options["access_log"] is False
        assert options["log_level"] == UVICORN_LOG_LEVEL

    def test_hands_the_application_and_its_options_to_uvicorn_which_refuses_a_taken_port(
        self, sign_in_settings, monkeypatch
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            monkeypatch.setenv("HOPIN_HOST", "127.0.0.1")
            monkeypatch.setenv("HOPIN_PORT", str(taken.getsockname()[1]))
            settings = ServerSettings()
            application = build_application(settings, fixture_page_build(), in_thread_stores())

            with pytest.raises(SystemExit) as refusal:
                run_uvicorn(application, uvicorn_options(settings))

        assert refusal.value.code != 0

    def test_leaves_uvicorn_no_logging_setup_of_its_own(self, sign_in_settings):
        assert uvicorn_options(sign_in_settings().settings)["log_config"] is None

    def test_leaves_the_client_address_to_the_trusted_proxy_setting(self, serve_over_test_stores):
        assert serve_over_test_stores()["proxy_headers"] is False

    def test_serves_participation_at_the_mcp_endpoint_for_agents_to_dial_in(self, sign_in_settings, monkeypatch):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)
        served = TestClient(
            build_application(ServerSettings(), fixture_page_build(), in_thread_stores()), base_url=TEST_PUBLIC_ORIGIN
        )

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
        served = TestClient(
            build_application(ServerSettings(), fixture_page_build(), in_thread_stores()), base_url=TEST_PUBLIC_ORIGIN
        )

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
        page_build = fixture_page_build()
        shared_stores = in_thread_stores()
        settings = ServerSettings()

        with pytest.raises(ValidationError):
            build_application(settings, page_build, shared_stores)

    def test_refuses_to_start_without_the_page_build_and_names_the_build_command(
        self, sign_in_settings, monkeypatch, tmp_path
    ):
        for name, value in sign_in_settings().environment.items():
            monkeypatch.setenv(name, value)

        with pytest.raises(StartRefusedError, match="make frontend"):
            main(["serve"], build_directory=tmp_path / "build")

    @pytest.mark.parametrize(
        ("sources", "starts"),
        [
            pytest.param('["https://figures.example.test/personas/"]', True, id="an allowed prefix starts"),
            pytest.param('["https://figures.example.test/personas"]', False, id="a malformed prefix refuses"),
        ],
    )
    def test_starts_only_with_well_formed_figure_sources(self, sources, starts, monkeypatch, serve_over_test_stores):
        monkeypatch.setenv("HOPIN_FIGURE_SOURCES", sources)

        if starts:
            serve_over_test_stores()
        else:
            with pytest.raises(ValidationError, match="figure_sources"):
                serve_over_test_stores()

    @pytest.mark.parametrize(
        ("setting", "value", "expected_line"),
        [
            pytest.param("HOPIN_REDIS_URL", None, "HOPIN_REDIS_URL is missing", id="missing_redis"),
            pytest.param("HOPIN_OIDC_ISSUER", None, "HOPIN_OIDC_ISSUER is missing", id="missing_issuer"),
            pytest.param(
                "HOPIN_PUBLIC_ORIGIN",
                "http://sentinel-origin.test/path",
                "HOPIN_PUBLIC_ORIGIN is malformed",
                id="malformed_origin",
            ),
            pytest.param(
                "HOPIN_FIGURE_SOURCES",
                '["https://sentinel-figures.test/personas"]',
                "HOPIN_FIGURE_SOURCES is malformed",
                id="malformed_figure_sources",
            ),
            pytest.param(
                "HOPIN_SPEECH__ORIGIN",
                "sentinel-speech",
                "HOPIN_SPEECH__ORIGIN must be",
                id="malformed_speech_origin",
            ),
            pytest.param(
                "HOPIN_TURN_PROVIDER",
                "cloudflare",
                "HOPIN_TURN_KEY_ID_FILE and HOPIN_TURN_API_TOKEN_FILE must be set when HOPIN_TURN_PROVIDER is",
                id="turn_without_key_files",
            ),
        ],
    )
    def test_refuses_a_missing_or_malformed_setting_in_one_line_naming_it_and_never_its_value(
        self, serve_refusal, setting, value, expected_line
    ):
        serve_refusal(setting, value, expected_line)

    @pytest.mark.parametrize("arguments", [[], ["frobnicate"]])
    def test_refuses_without_the_serve_command(self, arguments, capsys):
        with pytest.raises(SystemExit) as refusal:
            main(arguments)
        assert refusal.value.code != 0
        assert "serve" in capsys.readouterr().err
