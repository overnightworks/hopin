from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fastapi import FastAPI, Request
from webauth.config import install_web_auth_config, web_auth_config
from webauth.dependencies import ConnectionRefusal, admit_session, session_id_from_request

from hopin.auth.sign_in import (
    IssuerTransports,
    build_relying_party,
    build_sign_in_configs,
    install_sign_in,
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
        install_web_auth_config(app, configs.web_auth)
        install_sign_in(
            app,
            build_relying_party(configs, transports),
            stores.transaction,
            fixture_page_build(languages),
            RequestLanguage(languages, session_transaction_of(sessions), configs.web_auth),
            logout_button_label,
        )

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

        return SignInHarness(sign_in, issuer, accounts, sessions, configs.web_auth, app)

    return make


@pytest.fixture
def harness(make_harness) -> SignInHarness:
    return make_harness()
