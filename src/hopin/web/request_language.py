from typing import TYPE_CHECKING

from webauth.dependencies import account_language_of_session
from webauth.languages import AppLanguages, resolve_language

if TYPE_CHECKING:
    from fastapi import Request
    from webauth.config import WebAuthConfig
    from webauth.ports import SessionRecordStore


class RequestLanguage:
    """The one decider of a request's language: the session's account tag, then `Accept-Language`, then the default.

    The page answer, the sign-in start and the logout page all ask it, so they cannot disagree.
    """

    def __init__(self, languages: AppLanguages, sessions: SessionRecordStore, config: WebAuthConfig) -> None:
        self._languages = languages
        self._sessions = sessions
        self._config = config

    def __call__(self, request: Request) -> str:
        return resolve_language(
            self._languages,
            account_tag=account_language_of_session(request, sessions=self._sessions, config=self._config),
            accept_language=request.headers.get("accept-language"),
        )
