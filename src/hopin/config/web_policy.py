"""The values built on webauth's types, so that `constants` imports no webauth.

`SIGN_IN_PATHS` is the one source of the sign-in routes' paths: webauth derives from it the sign-in budget's paths
(logout included), the back-channel body bound (32 KiB) and the `no-store` rule, and `app.py` passes it to the
installer. The limits webauth then enforces:

- Sign-in requests have their own budget and every other path another (webauth's default: 600 requests per 60
  seconds per address); `/health` and the back-channel logout are exempt.
- Request bodies are capped at webauth's default of 4 KiB.
- A state-changing request is checked by its origin alone. webauth passes one marked `Sec-Fetch-Site: same-origin`, or
  one whose fetch site is absent or `same-site` and whose `Origin` names HopIn's host over https, on any port when
  `HOPIN_PUBLIC_ORIGIN` names none and on exactly that port when it names one. It refuses `cross-site`, any other
  fetch-site value, and a request with neither header, whatever its content type. Only Keycloak's back-channel logout
  is exempt, and there is no CSRF token or cookie.
"""

import re

from webauth.policies import PathRules
from webauth.sign_in_routes import SignInPaths

from hopin.config.constants import (
    APP_ASSET_PATH,
    AUTH_CALLBACK_PATH,
    AUTH_START_PATH,
    BACKCHANNEL_LOGOUT_PATH,
    CALL_FEATURES_PATH,
    CALL_PATH,
    HEALTH_PATH,
    HOME_PATH,
    IMMUTABLE_ASSET_DIRECTORY,
    LOGIN_PATH,
    LOGOUT_PATH,
    NO_ACCESS_PATH,
    PEOPLE_PATH,
)

SIGN_IN_PATHS = SignInPaths(
    start=AUTH_START_PATH, callback=AUTH_CALLBACK_PATH, logout=LOGOUT_PATH, backchannel_logout=BACKCHANNEL_LOGOUT_PATH
)
RETURN_TO_QUERY_PARAMETER = SIGN_IN_PATHS.login_redirect.redirect_query_param
# The health check is the edge's readiness probe.
RATE_LIMIT_EXEMPT_PATH_RULES = PathRules(exact=frozenset({HEALTH_PATH}))

PEOPLE_PATH_RULES = PathRules(prefixes=(f"{PEOPLE_PATH}/",))

# The immutable files of the page build set their own header on success (routes.py): a rule here would also
# stamp it onto the 401, 429 and 404 that the same path can answer. Every other build file is revalidated.
NO_CACHE_PATH_RULES = PathRules(
    exact=frozenset({HOME_PATH, LOGIN_PATH, NO_ACCESS_PATH, CALL_FEATURES_PATH}),
    prefixes=(f"{CALL_PATH}/",),
    patterns=(re.compile(rf"^{APP_ASSET_PATH}/(?!{IMMUTABLE_ASSET_DIRECTORY}/)"),),
)
