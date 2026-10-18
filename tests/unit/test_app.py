import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hopin.app import create_app
from hopin.config.settings import ServerSettings


class TestCreateApp:
    def test_returns_fastapi_application(self):
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_uses_provided_settings(self):
        settings = ServerSettings(port=9999)
        app = create_app(settings)
        assert app.state.settings.port == 9999

    def test_uses_default_settings_when_none(self):
        app = create_app()
        assert app.state.settings.port == 8080

    def test_registers_routes(self):
        response = TestClient(create_app()).get("/health")
        assert response.status_code == 200

    @pytest.mark.parametrize("path", ["/call", "/static/css/hopin.css", "/static/js/app.js"])
    def test_serves_the_page_and_its_assets(self, path):
        assert TestClient(create_app()).get(path).status_code == 200

    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_generated_documentation_is_not_served(self, path):
        response = TestClient(create_app()).get(path)
        assert response.status_code == 404
