from aiohttp import web

from hopin.app import create_app
from hopin.config.settings import ServerSettings


class TestCreateApp:
    def test_returns_aiohttp_application(self):
        app = create_app()
        assert isinstance(app, web.Application)

    def test_uses_provided_settings(self):
        settings = ServerSettings(port=9999)
        app = create_app(settings)
        assert app["settings"].port == 9999

    def test_uses_default_settings_when_none(self):
        app = create_app()
        assert app["settings"].port == 8080

    def test_registers_routes(self):
        app = create_app()
        routes = [resource.get_info() for resource in app.router.resources()]
        assert len(routes) >= 4
