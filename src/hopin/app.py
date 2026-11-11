import asyncio
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import structlog
from fastapi import FastAPI
from webauth.config import install_web_auth_config
from webauth.middleware import install_resource_metadata, install_web_auth_middleware
from webauth.policies import (
    BodySizePolicy,
    CacheControlRule,
    CspDirective,
    CsrfPolicy,
    RateLimitPolicy,
    SecurityHeadersPolicy,
    default_content_security_policy,
)

from hopin.auth.account import create_account_router
from hopin.auth.gate import bearer_gate, session_gate
from hopin.auth.sign_in import (
    ISSUER_OVER_HTTPS,
    IssuerTransports,
    build_relying_party,
    build_sign_in_configs,
    install_sign_in,
)
from hopin.auth.stores import SignInStores
from hopin.config.constants import (
    ANNOUNCER_STYLE_SOURCES,
    DEFAULT_REQUEST_BODY_MAX_BYTES,
    PERMISSIONS_POLICY,
    SESSION_SWEEP_SECONDS,
)
from hopin.config.secret_files import StartRefusedError
from hopin.config.web_policy import (
    DEFAULT_RATE_LIMIT_BUDGET,
    NO_CACHE_PATH_RULES,
    PEOPLE_PATH_RULES,
    RATE_LIMIT_EXEMPT_PATH_RULES,
    SIGN_IN_PATHS,
)
from hopin.people import create_people_router
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler, Scheduler, TimerHandle
from hopin.web.call_features import create_call_features_router
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.routes import create_router
from hopin.web.turn_relay import TURN_OVER_HTTPS, TurnCredentialsPost, build_turn_relay

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from fastapi import APIRouter
    from webauth.config import WebAuthConfig
    from webauth.middleware import ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionRecordStore, SignInTransaction

    from hopin.auth.stores import InMemorySessions
    from hopin.config.settings import ServerSettings
    from hopin.signaling.features import FeatureFactory
    from hopin.web.page_build import PageBuild

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class BuiltCallFeature(Protocol):
    """What a call feature adds to the app: the factory its signaling handler builds it with, its routes and the one
    bearer-token door it may open."""

    @property
    def factory(self) -> FeatureFactory:
        """The factory the signaling handler builds the feature with, once it has its rooms."""

    @property
    def router(self) -> APIRouter:
        """The routes the feature serves."""

    @property
    def bearer_resource(self) -> ProtectedResource | None:
        """The resource a bearer token reaches, with the gate admitting it, when the feature serves one."""


class CallFeatureBuilder(Protocol):
    """Builds an optional call feature on the app's own sign-in and scheduler; `main` passes each one in."""

    @property
    def sign_in_scopes(self) -> tuple[str, ...]:
        """The scopes the feature needs the person's sign-in to ask for."""

    @property
    def frame_sources(self) -> tuple[str, ...]:
        """The origins the page may frame for the feature; none keeps `frame-src 'none'`."""

    def __call__(
        self,
        *,
        relying_party: OidcRelyingParty,
        sessions: SessionRecordStore,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        transaction: SignInTransaction,
    ) -> BuiltCallFeature:
        """The feature's factory, its router and its bearer resource."""


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


def _lifespan(
    sessions: InMemorySessions,
    wall_clock: Callable[[], float],
    scheduler: Scheduler,
    signaling_handler: SignalingHandler,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        sweep = _ExpiredSessionSweep(sessions, wall_clock, scheduler)
        try:
            yield
        finally:
            sweep.stop()
            await signaling_handler.shut_down()

    return lifespan


def _configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            # The default exception formatter switches to rich tracebacks with locals as soon as rich is installed.
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback),
        ],
    )


def _install_web_auth(
    app: FastAPI,
    sessions: InMemorySessions,
    page_build: PageBuild,
    bearer: tuple[OidcRelyingParty, ProtectedResource] | None,
    call_features: Sequence[CallFeatureBuilder],
) -> None:
    frame_sources = tuple(source for builder in call_features for source in builder.frame_sources)
    install_web_auth_middleware(
        app,
        sign_in_paths=SIGN_IN_PATHS,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=default_content_security_policy(
                page_build.script_hashes,
                added_sources={CspDirective.STYLE_SRC: ANNOUNCER_STYLE_SOURCES, CspDirective.FRAME_SRC: frame_sources},
            ),
            permissions_policy=PERMISSIONS_POLICY,
            cache_control_rules=(
                CacheControlRule(paths=PEOPLE_PATH_RULES, value="no-store"),
                CacheControlRule(paths=NO_CACHE_PATH_RULES, value="no-cache"),
            ),
        ),
        csrf_origin=CsrfPolicy(),
        body_size=BodySizePolicy(default_max_bytes=DEFAULT_REQUEST_BODY_MAX_BYTES),
        rate_limit=RateLimitPolicy(
            default=DEFAULT_RATE_LIMIT_BUDGET,
            exempt=RATE_LIMIT_EXEMPT_PATH_RULES,
        ),
        bearer_gate=None if bearer is None else bearer_gate(*bearer),
        session_gate=session_gate(sessions, page_build.sign_in_assets, None if bearer is None else bearer[1]),
    )


def _include_feature_routes(app: FastAPI, built_features: list[BuiltCallFeature], issuer: str) -> None:
    for built in built_features:
        app.include_router(built.router)
        if built.bearer_resource is not None:
            install_resource_metadata(app, built.bearer_resource, issuer=issuer)


def _bearer_of(
    built_features: list[BuiltCallFeature], relying_party: OidcRelyingParty
) -> tuple[OidcRelyingParty, ProtectedResource] | None:
    resources = [built.bearer_resource for built in built_features if built.bearer_resource is not None]
    if len(resources) > 1:
        raise StartRefusedError("Only one call feature may serve a bearer-token resource")
    return (relying_party, resources[0]) if resources else None


def create_app(
    settings: ServerSettings,
    *,
    page_build: PageBuild,
    issuer_transports: IssuerTransports = ISSUER_OVER_HTTPS,
    stores: SignInStores | None = None,
    scheduler: Scheduler | None = None,
    turn_transport: TurnCredentialsPost = TURN_OVER_HTTPS,
    call_features: Sequence[CallFeatureBuilder] = (),
) -> FastAPI:
    sign_in_configs = build_sign_in_configs(
        settings, tuple(scope for builder in call_features for scope in builder.sign_in_scopes)
    )
    turn_relay = build_turn_relay(settings, turn_transport)

    _configure_logging()

    sign_in_stores = stores if stores is not None else SignInStores.in_memory()
    relying_party = build_relying_party(sign_in_configs, issuer_transports)
    room_manager = RoomManager(max_participants_per_room=settings.max_participants_per_room)
    timers = scheduler if scheduler is not None else AsyncioScheduler()
    built_features = [
        builder(
            relying_party=relying_party,
            sessions=sign_in_stores.sessions,
            auth_config=sign_in_configs.web_auth,
            scheduler=timers,
            transaction=sign_in_stores.transaction,
        )
        for builder in call_features
    ]
    bearer = _bearer_of(built_features, relying_party)
    signaling_handler = SignalingHandler(
        room_manager,
        SessionIdentityResolver(sign_in_stores.sessions),
        timers,
        [built.factory for built in built_features],
    )

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan(sign_in_stores.sessions, sign_in_configs.web_auth.wall_clock, timers, signaling_handler),
    )
    app.include_router(create_router(signaling_handler, room_manager, page_build))
    app.include_router(create_ice_servers_router(settings.stun_server, turn_relay))
    app.include_router(create_call_features_router(signaling_handler.features))
    _include_feature_routes(app, built_features, settings.oidc_issuer)
    app.include_router(create_account_router(settings.oidc_issuer))
    app.include_router(create_people_router(sign_in_stores.sessions, signaling_handler))
    app.state.settings = settings
    install_web_auth_config(app, sign_in_configs.web_auth)
    install_sign_in(app, relying_party, sign_in_stores.transaction, page_build)
    _install_web_auth(app, sign_in_stores.sessions, page_build, bearer, call_features)

    return app
