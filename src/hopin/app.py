import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
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
    RateLimitPolicy,
    RateLimitRule,
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
    BACKCHANNEL_LOGOUT_PATH_RULES,
    DEFAULT_RATE_LIMIT_BUDGET,
    DEFAULT_REQUEST_BODY_MAX_BYTES,
    NO_CACHE_PATH_RULES,
    PEOPLE_PATH_RULES,
    PERMISSIONS_POLICY,
    RATE_LIMIT_EXEMPT_PATH_RULES,
    SESSION_SWEEP_SECONDS,
    SIGN_IN_PATH_RULES,
    SIGN_IN_RATE_LIMIT_BUDGET,
)
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler, Scheduler, TimerHandle
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.people import create_people_router
from hopin.web.routes import create_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from hopin.auth.stores import InMemorySessions
    from hopin.config.settings import ServerSettings

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class _ExpiredSessionSweep:
    """Removes expired sessions every SESSION_SWEEP_SECONDS from start to stop."""

    def __init__(self, sessions: InMemorySessions, wall_clock: Callable[[], float], scheduler: Scheduler) -> None:
        self._sessions = sessions
        self._wall_clock = wall_clock
        self._scheduler = scheduler
        self._stopped = False
        self._timer = self._schedule()

    def _schedule(self) -> TimerHandle:
        return self._scheduler.call_later(SESSION_SWEEP_SECONDS, self._sweep)

    async def _sweep(self) -> None:
        if self._stopped:
            return
        # The scan holds the store's lock, so it runs off the event loop.
        removed = await asyncio.to_thread(
            self._sessions.delete_expired, datetime.fromtimestamp(int(self._wall_clock()), UTC)
        )
        if removed:
            logger.info("expired_sessions_swept", removed=removed)
        self._schedule_next_unless_stopped()

    def _schedule_next_unless_stopped(self) -> None:
        if not self._stopped:
            self._timer = self._schedule()

    def stop(self) -> None:
        self._stopped = True
        self._timer.cancel()


def _session_sweep_lifespan(
    sessions: InMemorySessions, wall_clock: Callable[[], float], scheduler: Scheduler
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        sweep = _ExpiredSessionSweep(sessions, wall_clock, scheduler)
        try:
            yield
        finally:
            sweep.stop()

    return lifespan


def create_app(
    settings: ServerSettings,
    *,
    issuer_transports: IssuerTransports = ISSUER_OVER_HTTPS,
    stores: SignInStores | None = None,
    scheduler: Scheduler | None = None,
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
    timers = scheduler if scheduler is not None else AsyncioScheduler()
    signaling_handler = SignalingHandler(room_manager, SessionIdentityResolver(sign_in_stores.sessions), timers)

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_session_sweep_lifespan(sign_in_stores.sessions, sign_in_configs.web_auth.wall_clock, timers),
    )
    app.include_router(create_router(signaling_handler, room_manager))
    app.include_router(create_ice_servers_router(settings.stun_server))
    app.include_router(create_account_router())
    app.include_router(create_people_router(sign_in_stores.sessions, signaling_handler))
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
            content_security_policy=default_content_security_policy(
                added_sources={CspDirective.FORM_ACTION: [end_session_origin(sign_in_configs.oidc_client)]}
            ),
            permissions_policy=PERMISSIONS_POLICY,
            cache_control_rules=(
                CacheControlRule(paths=BACKCHANNEL_LOGOUT_PATH_RULES, value="no-store"),
                CacheControlRule(paths=PEOPLE_PATH_RULES, value="no-store"),
                CacheControlRule(paths=NO_CACHE_PATH_RULES, value="no-cache"),
            ),
        ),
        csrf_origin=CsrfPolicy(exempt=BACKCHANNEL_LOGOUT_PATH_RULES),
        body_size=BodySizePolicy(
            default_max_bytes=DEFAULT_REQUEST_BODY_MAX_BYTES,
            rules=(BodySizeRule(paths=BACKCHANNEL_LOGOUT_PATH_RULES, max_bytes=BACKCHANNEL_LOGOUT_BODY_MAX_BYTES),),
        ),
        rate_limit=RateLimitPolicy(
            default=DEFAULT_RATE_LIMIT_BUDGET,
            rules=(RateLimitRule(paths=SIGN_IN_PATH_RULES, budget=SIGN_IN_RATE_LIMIT_BUDGET),),
            exempt=RATE_LIMIT_EXEMPT_PATH_RULES,
        ),
        session_gate=session_gate(sign_in_stores.sessions),
    )

    return app
