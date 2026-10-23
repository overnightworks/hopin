import secrets
import traceback

import pytest
from webauth.config import installed_web_auth_config

from hopin.app import create_app
from hopin.auth.sign_in import SignInConfigurationError, build_sign_in_configs
from hopin.config.secret_files import StartRefusedError

SECRET_FILE_SETTINGS = ["oidc_client_secret_file", "session_secret_file"]
SESSION_LENGTH_SECONDS = 12 * 60 * 60


def corrupt_secret_file(settings, setting, state, sentinel):
    path = getattr(settings, setting)
    if state == "missing":
        path.unlink()
    elif state == "unreadable":
        path.unlink()
        path.mkdir()
    elif state == "empty":
        path.write_text("")
    elif state == "only a newline":
        path.write_text("\n")
    elif state == "not text":
        path.write_bytes(sentinel.encode() + b"\xff\xfe")


class TestSecretFiles:
    @pytest.mark.parametrize("setting", SECRET_FILE_SETTINGS)
    @pytest.mark.parametrize("state", ["missing", "unreadable", "empty", "only a newline", "not text"])
    def test_a_secret_file_that_cannot_be_used_stops_the_start_without_leaking(
        self, sign_in_settings, capsys, setting, state
    ):
        settings = sign_in_settings().settings
        sentinel = secrets.token_urlsafe(16)
        corrupt_secret_file(settings, setting, state, sentinel)

        with pytest.raises(StartRefusedError, match=setting) as refusal:
            build_sign_in_configs(settings)

        error = refusal.value
        assert error.__cause__ is None
        assert error.__suppress_context__ is True
        rendered = "".join(traceback.format_exception(error))
        assert sentinel not in rendered
        assert str(getattr(settings, setting)) not in rendered
        assert "Errno" not in rendered
        assert capsys.readouterr() == ("", "")

    def test_a_session_secret_of_31_characters_is_refused_naming_its_setting(self, sign_in_settings):
        settings = sign_in_settings().settings
        settings.session_secret_file.write_text(secrets.token_urlsafe(24)[:31])
        with pytest.raises(SignInConfigurationError, match="session_secret_file"):
            build_sign_in_configs(settings)

    def test_a_session_secret_of_32_characters_is_accepted(self, sign_in_settings):
        settings = sign_in_settings().settings
        settings.session_secret_file.write_text(secrets.token_urlsafe(24)[:32])
        build_sign_in_configs(settings)

    def test_each_secret_loses_one_trailing_newline(self, sign_in_settings):
        sign_in = sign_in_settings()
        configs = build_sign_in_configs(sign_in.settings)
        assert configs.oidc_client.client_secret.get_secret_value() == sign_in.client_secret
        assert configs.web_auth.session_secret.get_secret_value() == sign_in.session_secret

    @pytest.mark.parametrize(
        ("ending", "expected_ending"),
        [
            ("\rinside\n", "\rinside"),
            (" \n", " "),
            ("\n\n", "\n"),
            (" ", " "),
            ("\r\n", "\r"),
        ],
    )
    def test_a_secret_file_keeps_everything_but_its_final_newline(self, sign_in_settings, ending, expected_ending):
        settings = sign_in_settings().settings
        secret = secrets.token_urlsafe(36)
        settings.oidc_client_secret_file.write_bytes((secret + ending).encode())
        settings.session_secret_file.write_bytes((secret + ending).encode())

        configs = build_sign_in_configs(settings)

        expected = secret + expected_ending
        assert configs.oidc_client.client_secret.get_secret_value() == expected
        assert configs.web_auth.session_secret.get_secret_value() == expected

    def test_no_representation_shows_a_secret_or_a_secret_file_path(self, sign_in_settings):
        sign_in = sign_in_settings()
        representations = repr(sign_in) + repr(sign_in.settings)
        for hidden in (
            sign_in.client_secret,
            sign_in.session_secret,
            str(sign_in.settings.oidc_client_secret_file),
            str(sign_in.settings.session_secret_file),
        ):
            assert hidden not in representations


class TestRefusedUrls:
    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("oidc_issuer", "http://issuer.test.invalid/realms/test"),
            ("public_origin", "http://hopin.test.invalid"),
        ],
    )
    def test_an_http_url_stops_the_start_naming_the_settings_and_not_the_value(self, sign_in_settings, setting, value):
        settings = sign_in_settings(**{setting: value}).settings
        with pytest.raises(SignInConfigurationError, match="oidc_issuer and public_origin") as refusal:
            build_sign_in_configs(settings)
        assert value not in str(refusal.value)


class TestBuiltConfigs:
    def test_the_client_registers_the_callback_and_the_post_logout_page_under_the_public_origin(self, sign_in_settings):
        sign_in = sign_in_settings()
        client = build_sign_in_configs(sign_in.settings).oidc_client
        origin = sign_in.settings.public_origin
        assert client.redirect_uri == f"{origin}/auth/callback"
        assert client.post_logout_redirect_uri == f"{origin}/login"
        assert client.client_id == sign_in.settings.oidc_client_id

    def test_the_client_endpoints_derive_from_the_issuer(self, sign_in_settings):
        sign_in = sign_in_settings()
        client = build_sign_in_configs(sign_in.settings).oidc_client
        issuer = sign_in.settings.oidc_issuer
        assert client.issuer == issuer
        assert client.authorization_endpoint == f"{issuer}/protocol/openid-connect/auth"
        assert client.token_endpoint == f"{issuer}/protocol/openid-connect/token"
        assert client.jwks_url == f"{issuer}/protocol/openid-connect/certs"
        assert client.end_session_endpoint == f"{issuer}/protocol/openid-connect/logout"

    def test_a_session_lasts_twelve_hours_in_a_secure_lax_cookie(self, sign_in_settings):
        web_auth = installed_web_auth_config(create_app(sign_in_settings().settings))
        assert web_auth.session_lifetime.idle_seconds == SESSION_LENGTH_SECONDS
        assert web_auth.session_lifetime.absolute_max_age_seconds == SESSION_LENGTH_SECONDS
        assert web_auth.cookie_samesite == "lax"
        assert web_auth.cookie_secure is True

    def test_only_the_public_origins_host_is_admitted(self, sign_in_settings):
        settings = sign_in_settings(public_origin="https://HopIn.Test.invalid:8443").settings
        web_auth = installed_web_auth_config(create_app(settings))
        assert web_auth.allowed_hosts_exact == frozenset({"hopin.test.invalid:8443"})
        assert web_auth.allowed_hosts_patterns == ()
