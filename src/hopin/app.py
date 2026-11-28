import asyncio
import secrets
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
from webauth.ports import StoreUnavailableError
from webauth.postgres import SessionStoreError
from webauth.web_auth import WebAuthStores

from hopin.auth.account import create_account_router
from hopin.auth.gate import bearer_gate, session_gate
from hopin.auth.sign_in import (
    ISSUER_OVER_HTTPS,
    IssuerTransports,
    SignInConfigs,
    build_sign_in_configs,
    build_web_auth,
    create_pages_router,
)
from hopin.config.constants import (
    HOLDER_ID_BYTES,
    PERMISSIONS_POLICY,
    SESSION_SWEEP_SECONDS,
)
from hopin.config.secret_files import StartRefusedError
from hopin.config.web_policy import (
    NO_CACHE_PATH_RULES,
    PEOPLE_PATH_RULES,
)
from hopin.people import create_people_router
from hopin.signaling.call_owner import CallOwner
from hopin.signaling.call_store import CallStore, ProcessLease, call_leases
from hopin.signaling.handler import SignalingHandler
from hopin.signaling.identity import SessionIdentityResolver
from hopin.signaling.scheduler import AsyncioScheduler, Deadlines, InMemoryDeadlines, Scheduler, TimerHandle
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

    from hopin.config.settings import RealmSettings, ServerSettings
    from hopin.signaling.features import TransitionFeature
    from hopin.stores.identity import IdentityStores
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
        turn_relay: TurnRelay | None,
    ) -> BuiltCallFeature:
        """The feature, its router and its bearer resource; `turn_relay` is the TURN credential source, none with TURN
        off."""


class _ExpiredSessionSweep:
    """Removes expired sessions every SESSION_SWEEP_SECONDS from start to stop."""

    def __init__(self, identity: IdentityStores, wall_clock: Callable[[], float], scheduler: Scheduler) -> None:
        self._identity = identity
        self._wall_clock = wall_clock
        self._scheduler = scheduler
        self._stopped = False
        self._timer = self._schedule()

    def _schedule(self) -> TimerHandle:
        return self._scheduler.call_later(SESSION_SWEEP_SECONDS, self._sweep)

    async def _sweep(self) -> None:
        if self._stopped:
            return
        # The store call blocks, so it runs off the event loop; a sweep the store refused deleted nothing and the next
        # cycle retries it.
        try:
            removed = await asyncio.to_thread(
                self._identity.delete_expired, datetime.fromtimestamp(int(self._wall_clock()), UTC)
            )
        except SessionStoreError, StoreUnavailableError:
            logger.warning("sessions.sweep_refused")
        else:
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
    identity: IdentityStores,
    wall_clock: Callable[[], float],
    scheduler: Scheduler,
    signaling_handler: SignalingHandler,
    call_owner: CallOwner,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        sweep = _ExpiredSessionSweep(identity, wall_clock, scheduler)
        try:
            # The process holds its calls from its lease until its stop frees them, so a stop frees by the lapse's path.
            async with call_owner.serving():
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
    identity: IdentityStores,
    page_build: PageBuild,
    request_language: RequestLanguage,
    shared_stores: SharedStores,
    turn_relay: TurnRelay | None,
    lease: ProcessLease,
) -> None:
    fixed_feature_routers, templated_feature_routers = _split_by_path_form(built_features)
    call_core_routers = [
        create_router(
            signaling_handler, page_build, request_language, lambda: lease.serving and shared_stores.answers()
        ),
        create_ice_servers_router(settings.stun_server, turn_relay),
        create_call_features_router(signaling_handler.features),
    ]
    for router in (*fixed_feature_routers, *call_core_routers, *templated_feature_routers):
        app.include_router(router)
    app.include_router(create_pages_router(page_build, request_language))
    app.include_router(create_account_router(settings.oidc_issuer))
    app.include_router(create_people_router(identity, signaling_handler))


def _bearer_of(built_features: list[BuiltCallFeature]) -> BearerGate | None:
    resources = [built.bearer_resource for built in built_features if built.bearer_resource is not None]
    if len(resources) > 1:
        raise StartRefusedError("Only one call feature may serve a bearer-token resource")
    return bearer_gate(resources[0]) if resources else None


def _new_app(
    settings: ServerSettings,
    identity: IdentityStores,
    auth_config: WebAuthConfig,
    timers: Scheduler,
    deadlines: Deadlines,
    built_features: list[BuiltCallFeature],
    shared_stores: SharedStores,
) -> tuple[FastAPI, SignalingHandler, ProcessLease]:
    redis = shared_stores.async_redis
    lease = ProcessLease(call_leases(redis), holder=secrets.token_hex(HOLDER_ID_BYTES))
    signaling_handler = SignalingHandler(
        CallStore(redis, lease, place_limit=settings.max_participants_per_room),
        SessionIdentityResolver(),
        timers,
        deadlines,
        [built.feature for built in built_features],
    )
    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan(
            identity, auth_config.wall_clock, timers, signaling_handler, CallOwner(signaling_handler, lease)
        ),
    )
    return app, signaling_handler, lease


def _build_features(
    call_features: Sequence[CallFeatureBuilder],
    relying_party: OidcRelyingParty,
    session_transaction: SessionTransaction,
    auth_config: WebAuthConfig,
    timers: Scheduler,
    deadlines: Deadlines,
    request_language: RequestLanguage,
    page_build: PageBuild,
    turn_relay: TurnRelay | None,
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
            turn_relay=turn_relay,
        )
        for builder in call_features
    ]


def _assemble_app(
    settings: ServerSettings,
    *,
    page_build: PageBuild,
    shared_stores: SharedStores,
    sign_in_configs: SignInConfigs,
    identity: IdentityStores,
    issuer_transports: IssuerTransports,
    monotonic_clock: Callable[[], float],
    timers: Scheduler,
    deadlines: Deadlines,
    turn_relay: TurnRelay | None,
    call_features: Sequence[CallFeatureBuilder],
) -> FastAPI:
    session_transaction = identity.session_transaction
    request_language = RequestLanguage(page_build.languages, session_transaction, sign_in_configs.web_auth)
    auth = build_web_auth(
        sign_in_configs,
        WebAuthStores(sign_in=identity.sign_in_transaction, sessions=session_transaction),
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
        turn_relay,
    )
    bearer = _bearer_of(built_features)
    app, signaling_handler, lease = _new_app(
        settings, identity, sign_in_configs.web_auth, timers, deadlines, built_features, shared_stores
    )
    _include_routes(
        app,
        settings,
        built_features,
        signaling_handler,
        identity,
        page_build,
        request_language,
        shared_stores,
        turn_relay,
        lease,
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
    identity: IdentityStores,
    issuer_transports: IssuerTransports = ISSUER_OVER_HTTPS,
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

    timers = scheduler if scheduler is not None else AsyncioScheduler()
    deadlines = InMemoryDeadlines(timers)
    return _assemble_app(
        settings,
        page_build=page_build,
        shared_stores=shared_stores,
        sign_in_configs=sign_in_configs,
        identity=identity,
        issuer_transports=issuer_transports,
        monotonic_clock=monotonic_clock,
        timers=timers,
        deadlines=deadlines,
        turn_relay=turn_relay,
        call_features=call_features,
    )
