from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, Request
from webauth.config import web_auth_config
from webauth.dependencies import ConnectionRefusal, admit_session, session_id_from_request
from webauth.policies import SecurityHeadersPolicy, default_content_security_policy
from webauth.web_auth import WebAuthStores

from hopin.auth.sign_in import (
    IssuerTransports,
    build_sign_in_configs,
    build_web_auth,
    create_pages_router,
    words_logout_button_label,
)
from hopin.stores.session_transaction import session_transaction_of
from hopin.web.page_build import APP_LANGUAGES
from hopin.web.request_language import RequestLanguage
from tests.support.fake_issuer import FakeIssuer
from tests.support.in_thread_stores import in_thread_stores
from tests.support.page_build import fixture_page_build
from tests.support.sign_in_harness import WHO_AM_I_PATH, SignInHarness
from tests.support.stores import single_process_stores

if TYPE_CHECKING:
    from collections.abc import Callable

    from webauth.languages import AppLanguages
    from webauth.ports import AuditSink


@pytest.fixture
def make_harness(sign_in_settings) -> Callable[..., SignInHarness]:
    def make(
        *,
        key_set_of: FakeIssuer | None = None,
        windows_line_ending: bool = False,
        client_secret: str | None = None,
        audit: AuditSink | None = None,
        languages: AppLanguages = APP_LANGUAGES,
        logout_button_label: Callable[[str], str] = words_logout_button_label,
    ) -> SignInHarness:
        sign_in = sign_in_settings()
        if client_secret is not None:
            sign_in = replace(sign_in, client_secret=client_secret)
        sign_in.settings.oidc_client_secret_file.write_text(
            sign_in.client_secret + ("\r\n" if windows_line_ending else "\n")
        )
        configs = build_sign_in_configs(sign_in.settings, (), in_thread_stores().rate_limits)
        issuer = FakeIssuer(sign_in)
        transports = issuer.transports
        if key_set_of is not None:
            transports = IssuerTransports(transports.post_token_request, key_set_of.fetch_key_set)
        stores = single_process_stores(audit)
        accounts, sessions = stores.accounts, stores.sessions
        app = FastAPI()
        page_build = fixture_page_build(languages)
        session_transaction = session_transaction_of(sessions)
        request_language = RequestLanguage(languages, session_transaction, configs.web_auth)
        web_auth = build_web_auth(
            configs,
            WebAuthStores(sign_in=stores.transaction, sessions=session_transaction),
            page_build,
            request_language,
            SecurityHeadersPolicy(content_security_policy=default_content_security_policy()),
            transports,
            logout_button_label=logout_button_label,
        )
        app.include_router(create_pages_router(page_build, request_language))

        @app.get(WHO_AM_I_PATH)
        def who_am_i(request: Request) -> dict[str, object]:
            config = web_auth_config(request)
            session_id = session_id_from_request(request, config)
            if session_id is None:
                return {"user": None}
            admitted = admit_session(session_id, sessions=sessions, config=config)
            if isinstance(admitted, ConnectionRefusal):
                return {"user": None}
            return {"user": {"id": admitted.id, "username": admitted.username}, "session_id": session_id}

        web_auth.install(app, bearer=None)
        return SignInHarness(sign_in, issuer, accounts, sessions, configs.web_auth, app)

    return make


@pytest.fixture
def harness(make_harness) -> SignInHarness:
    return make_harness()
