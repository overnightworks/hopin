from typing import TYPE_CHECKING

from webauth.dependencies import account_language_of_session
from webauth.languages import AppLanguages, resolve_language

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection
    from webauth.config import WebAuthConfig
    from webauth.ports import SessionTransaction


class RequestLanguage:
    """The one decider of a connection's language: the session's account tag, then `Accept-Language`, then the
    default.

    The page answer, the sign-in start, the logout page and the game master's runs all ask it, so they cannot disagree.
    """

    def __init__(self, languages: AppLanguages, session_transaction: SessionTransaction, config: WebAuthConfig) -> None:
        self._languages = languages
        self._session_transaction = session_transaction
        self._config = config

    def __call__(self, connection: HTTPConnection) -> str:
        with self._session_transaction() as sessions:
            account_tag = account_language_of_session(connection, sessions=sessions, config=self._config)
        return resolve_language(
            self._languages,
            account_tag=account_tag,
            accept_language=connection.headers.get("accept-language"),
        )
