from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from webauth.config import install_web_auth_config
from webauth.middleware import install_web_auth_middleware
from webauth.policies import (
    BodySizePolicy,
    BodySizeRule,
    CacheControlRule,
    CspDirective,
    CsrfPolicy,
    PathRules,
    SecurityHeadersPolicy,
    default_content_security_policy,
)

from hopin.auth.account import create_account_router
from hopin.auth.gate import session_gate
from hopin.auth.sign_in import (
    ISSUER_OVER_HTTPS,
    IssuerTransports,
    build_sign_in_configs,
    create_sign_in_router,
    end_session_origin,
)
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    BACKCHANNEL_LOGOUT_BODY_MAX_BYTES,
    BACKCHANNEL_LOGOUT_PATH,
    DEFAULT_REQUEST_BODY_MAX_BYTES,
    PERMISSIONS_POLICY,
)
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.routes import create_router

if TYPE_CHECKING:
    from hopin.config.settings import ServerSettings


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
    app.include_router(create_account_router(sign_in_stores.sessions))
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
    backchannel_logout_path = PathRules(exact=frozenset({BACKCHANNEL_LOGOUT_PATH}))
    install_web_auth_middleware(
        app,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=default_content_security_policy(
                added_sources={CspDirective.FORM_ACTION: [end_session_origin(sign_in_configs.oidc_client)]}
            ),
            permissions_policy=PERMISSIONS_POLICY,
            cache_control_rules=(CacheControlRule(paths=backchannel_logout_path, value="no-store"),),
        ),
        csrf_origin=CsrfPolicy(exempt=backchannel_logout_path),
        body_size=BodySizePolicy(
            default_max_bytes=DEFAULT_REQUEST_BODY_MAX_BYTES,
            rules=(BodySizeRule(paths=backchannel_logout_path, max_bytes=BACKCHANNEL_LOGOUT_BODY_MAX_BYTES),),
        ),
        session_gate=session_gate(sign_in_stores.sessions),
    )

    return app
