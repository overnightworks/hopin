from aiohttp import web

from openstream.app import create_app
from openstream.config.settings import ServerSettings


def main() -> None:
    settings = ServerSettings()
    app = create_app(settings)
    web.run_app(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
