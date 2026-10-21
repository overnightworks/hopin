import structlog
from fastapi import FastAPI
from webauth.config import install_web_auth_config
from webauth.middleware import install_web_auth_middleware
from webauth.policies import SecurityHeadersPolicy, default_content_security_policy

from hopin.auth.gate import session_gate
from hopin.auth.sign_in import ISSUER_OVER_HTTPS, IssuerTransports, build_sign_in_configs, create_sign_in_router
from hopin.auth.stores import SignInStores
from hopin.config.constants import PERMISSIONS_POLICY
from hopin.config.settings import ServerSettings
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.routes import create_router


def create_app(
    settings: ServerSettings,
    *,
    issuer_transports: IssuerTransports = ISSUER_OVER_HTTPS,
    stores: SignInStores | None = None,
) -> FastAPI:
    sign_in_configs = build_sign_in_configs(settings)

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            # The default exception formatter switches to rich tracebacks with locals as soon as rich is installed.
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback),
        ],
    )

    sign_in_stores = stores if stores is not None else SignInStores.in_memory()
    room_manager = RoomManager(max_participants_per_room=settings.max_participants_per_room)
    signaling_handler = SignalingHandler(
        room_manager, SessionIdentityResolver(sign_in_stores.sessions), AsyncioScheduler()
    )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(create_router(signaling_handler, room_manager))
    app.include_router(create_ice_servers_router(settings.stun_server))
    app.state.settings = settings
    install_web_auth_config(app, sign_in_configs.web_auth)
    app.include_router(
        create_sign_in_router(
            sign_in_configs.oidc_client,
            sign_in_configs.web_auth,
            issuer_transports,
            accounts=sign_in_stores.accounts,
            sessions=sign_in_stores.sessions,
            lock=sign_in_stores.lock,
            audit=sign_in_stores.audit,
        )
    )
    install_web_auth_middleware(
        app,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=default_content_security_policy(), permissions_policy=PERMISSIONS_POLICY
        ),
        session_gate=session_gate(sign_in_stores.sessions),
    )

    return app
