import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

import structlog
from fastapi import APIRouter, FastAPI
from webauth.policies import (
    CacheControlRule,
    CspDirective,
    SecurityHeadersPolicy,
    default_content_security_policy,
)
from webauth.single_process_stores import SingleProcessStores
from webauth.web_auth import WebAuthStores

from hopin.auth.account import create_account_router
from hopin.auth.gate import bearer_gate, session_gate
from hopin.auth.sign_in import (
    ISSUER_OVER_HTTPS,
    IssuerTransports,
    LoggingAuditSink,
    SignInConfigs,
    build_sign_in_configs,
    build_web_auth,
    create_pages_router,
)
from hopin.config.constants import (
    PERMISSIONS_POLICY,
    SESSION_SWEEP_SECONDS,
)
from hopin.config.secret_files import StartRefusedError
from hopin.config.web_policy import (
    NO_CACHE_PATH_RULES,
    PEOPLE_PATH_RULES,
)
from hopin.people import create_people_router
from hopin.rooms.manager import RoomManager
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler, Deadlines, InMemoryDeadlines, Scheduler, TimerHandle
from hopin.stores.session_transaction import session_transaction_of
from hopin.web.call_features import create_call_features_router
from hopin.web.ice_servers import create_ice_servers_router
from hopin.web.request_language import RequestLanguage
from hopin.web.routes import create_router
from hopin.web.turn_relay import TURN_OVER_HTTPS, TurnCredentialsPost, build_turn_relay

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Sequence

    from starlette.routing import BaseRoute
    from webauth.config import WebAuthConfig
    from webauth.middleware import BearerGate, ProtectedResource
    from webauth.oidc import OidcRelyingParty
    from webauth.ports import SessionTransaction
    from webauth.single_process_stores import SingleProcessSessionStore

    from hopin.config.settings import RealmSettings, ServerSettings
    from hopin.signaling.features import TransitionFeature
    from hopin.stores.shared import SharedStores
    from hopin.web.ice_servers import TurnRelay
    from hopin.web.page_build import PageBuild

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class BuiltCallFeature(Protocol):
    """What a call feature adds to the app: the feature its signaling handler runs, its routes and the one bearer-token
    door it may open."""

    @property
    def feature(self) -> TransitionFeature:
        """The feature the signaling handler runs on its transitions."""

    @property
    def router(self) -> APIRouter:
        """The routes the feature serves."""

    @property
    def bearer_resource(self) -> ProtectedResource | None:
        """The resource a bearer token reaches, with the gate admitting it, when the feature serves one."""


class CallFeatureBuilder(Protocol):
    """Builds an optional call feature on the app's own sign-in, scheduler, deadlines and request language; `main`
    passes each one in."""

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
        session_transaction: SessionTransaction,
        auth_config: WebAuthConfig,
        scheduler: Scheduler,
        deadlines: Deadlines,
        request_language: RequestLanguage,
        page_build: PageBuild,
    ) -> BuiltCallFeature:
        """The feature, its router and its bearer resource."""


class _ExpiredSessionSweep:
    """Removes expired sessions every SESSION_SWEEP_SECONDS from start to stop."""

    def __init__(
        self, sessions: SingleProcessSessionStore, wall_clock: Callable[[], float], scheduler: Scheduler
    ) -> None:
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
            logger.info("sessions.swept", removed_count=removed)
        self._schedule_next_unless_stopped()

    def _schedule_next_unless_stopped(self) -> None:
        if not self._stopped:
            self._timer = self._schedule()

    def stop(self) -> None:
        self._stopped = True
        self._timer.cancel()


def _lifespan(
    sessions: SingleProcessSessionStore,
    wall_clock: Callable[[], float],
    scheduler: Scheduler,
    signaling_handler: SignalingHandler,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        sweep = _ExpiredSessionSweep(sessions, wall_clock, scheduler)
        try:
            logger.info("process.ready")
            yield
        finally:
            sweep.stop()
            await signaling_handler.shut_down()
            logger.info("process.stopped")

    return lifespan


def _security_headers(page_build: PageBuild, call_features: Sequence[CallFeatureBuilder]) -> SecurityHeadersPolicy:
    frame_sources = tuple(source for builder in call_features for source in builder.frame_sources)
    return SecurityHeadersPolicy(
        content_security_policy=default_content_security_policy(
            page_build.script_hashes,
            added_sources={
                CspDirective.STYLE_SRC: page_build.announcer_style_sources,
                CspDirective.FRAME_SRC: frame_sources,
            },
        ),
        permissions_policy=PERMISSIONS_POLICY,
        cache_control_rules=(
            CacheControlRule(paths=PEOPLE_PATH_RULES, value="no-store"),
            CacheControlRule(paths=NO_CACHE_PATH_RULES, value="no-cache"),
        ),
    )


def _is_templated(route: BaseRoute) -> bool:
    return "{" in getattr(route, "path", "{")


def _split_by_path_form(built_features: list[BuiltCallFeature]) -> tuple[list[APIRouter], list[APIRouter]]:
    """Each feature's routes in two routers: those with a fixed path, which go ahead of the call core's routes and keep
    the feature's lifespan, and those with a path parameter, which go after them so they never take a core address."""
    fixed_routers: list[APIRouter] = []
    templated_routers: list[APIRouter] = []
    for built in built_features:
        fixed = APIRouter(lifespan=built.router.lifespan_context)
        templated = APIRouter()
        fixed.routes.extend(route for route in built.router.routes if not _is_templated(route))
        templated.routes.extend(route for route in built.router.routes if _is_templated(route))
        fixed_routers.append(fixed)
        templated_routers.append(templated)
    return fixed_routers, templated_routers


def _include_routes(
    app: FastAPI,
    settings: ServerSettings,
    built_features: list[BuiltCallFeature],
    signaling_handler: SignalingHandler,
    sessions: SingleProcessSessionStore,
    page_build: PageBuild,
    request_language: RequestLanguage,
    shared_stores: SharedStores,
    turn_relay: TurnRelay | None,
) -> None:
    fixed_feature_routers, templated_feature_routers = _split_by_path_form(built_features)
    call_core_routers = [
        create_router(signaling_handler, page_build, request_language, shared_stores.answers),
        create_ice_servers_router(settings.stun_server, turn_relay),
        create_call_features_router(signaling_handler.features),
    ]
    for router in (*fixed_feature_routers, *call_core_routers, *templated_feature_routers):
        app.include_router(router)
    app.include_router(create_pages_router(page_build, request_language))
    app.include_router(create_account_router(settings.oidc_issuer))
    app.include_router(create_people_router(sessions, signaling_handler))


def _bearer_of(built_features: list[BuiltCallFeature]) -> BearerGate | None:
    resources = [built.bearer_resource for built in built_features if built.bearer_resource is not None]
    if len(resources) > 1:
        raise StartRefusedError("Only one call feature may serve a bearer-token resource")
    return bearer_gate(resources[0]) if resources else None


def _new_app(
    settings: ServerSettings,
    sign_in_stores: SingleProcessStores,
    auth_config: WebAuthConfig,
    timers: Scheduler,
    deadlines: Deadlines,
    built_features: list[BuiltCallFeature],
) -> tuple[FastAPI, SignalingHandler]:
    signaling_handler = SignalingHandler(
        RoomManager(max_participants_per_room=settings.max_participants_per_room),
        SessionIdentityResolver(),
        timers,
        deadlines,
        [built.feature for built in built_features],
    )
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan(sign_in_stores.sessions, auth_config.wall_clock, timers, signaling_handler),
    )
    return app, signaling_handler


def _build_features(
    call_features: Sequence[CallFeatureBuilder],
    relying_party: OidcRelyingParty,
    session_transaction: SessionTransaction,
    auth_config: WebAuthConfig,
    timers: Scheduler,
    deadlines: Deadlines,
    request_language: RequestLanguage,
    page_build: PageBuild,
) -> list[BuiltCallFeature]:
    return [
        builder(
            relying_party=relying_party,
            session_transaction=session_transaction,
            auth_config=auth_config,
            scheduler=timers,
            deadlines=deadlines,
            request_language=request_language,
            page_build=page_build,
        )
        for builder in call_features
    ]


def _assemble_app(
    settings: ServerSettings,
    *,
    page_build: PageBuild,
    shared_stores: SharedStores,
    sign_in_configs: SignInConfigs,
    sign_in_stores: SingleProcessStores,
    issuer_transports: IssuerTransports,
    monotonic_clock: Callable[[], float],
    timers: Scheduler,
    deadlines: Deadlines,
    turn_relay: TurnRelay | None,
    call_features: Sequence[CallFeatureBuilder],
) -> FastAPI:
    session_transaction = session_transaction_of(sign_in_stores.sessions)
    request_language = RequestLanguage(page_build.languages, session_transaction, sign_in_configs.web_auth)
    auth = build_web_auth(
        sign_in_configs,
        WebAuthStores(sign_in=sign_in_stores.transaction, sessions=session_transaction),
        page_build,
        request_language,
        _security_headers(page_build, call_features),
        issuer_transports,
        monotonic_clock,
        gate=session_gate(page_build.sign_in_assets),
    )
    built_features = _build_features(
        call_features,
        auth.relying_party,
        session_transaction,
        sign_in_configs.web_auth,
        timers,
        deadlines,
        request_language,
        page_build,
    )
    bearer = _bearer_of(built_features)
    app, signaling_handler = _new_app(
        settings, sign_in_stores, sign_in_configs.web_auth, timers, deadlines, built_features
    )
    _include_routes(
        app,
        settings,
        built_features,
        signaling_handler,
        sign_in_stores.sessions,
        page_build,
        request_language,
        shared_stores,
        turn_relay,
    )
    app.state.settings = settings
    auth.install(app, bearer=bearer)
    return app


def create_app(
    settings: ServerSettings,
    realm: RealmSettings,
    *,
    page_build: PageBuild,
    shared_stores: SharedStores,
    issuer_transports: IssuerTransports = ISSUER_OVER_HTTPS,
    stores: SingleProcessStores | None = None,
    scheduler: Scheduler | None = None,
    turn_transport: TurnCredentialsPost = TURN_OVER_HTTPS,
    call_features: Sequence[CallFeatureBuilder] = (),
    monotonic_clock: Callable[[], float] = time.monotonic,
    wall_clock: Callable[[], float] = time.time,
) -> FastAPI:
    sign_in_configs = build_sign_in_configs(
        settings,
        realm,
        tuple(scope for builder in call_features for scope in builder.sign_in_scopes),
        shared_stores.rate_limits,
        wall_clock,
    )
    turn_relay = build_turn_relay(settings, turn_transport)

    sign_in_stores = stores if stores is not None else SingleProcessStores(audit=LoggingAuditSink())
    timers = scheduler if scheduler is not None else AsyncioScheduler()
    deadlines = InMemoryDeadlines(timers)
    return _assemble_app(
        settings,
        page_build=page_build,
        shared_stores=shared_stores,
        sign_in_configs=sign_in_configs,
        sign_in_stores=sign_in_stores,
        issuer_transports=issuer_transports,
        monotonic_clock=monotonic_clock,
        timers=timers,
        deadlines=deadlines,
        turn_relay=turn_relay,
        call_features=call_features,
    )
