"""The values built on webauth's types, so that `constants` imports no webauth."""

import re

from webauth.policies import PathRules, RateLimitBudget
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
    STATIC_PATH,
)

SIGN_IN_PATHS = SignInPaths(
    start=AUTH_START_PATH, callback=AUTH_CALLBACK_PATH, logout=LOGOUT_PATH, backchannel_logout=BACKCHANNEL_LOGOUT_PATH
)
RETURN_TO_QUERY_PARAMETER = SIGN_IN_PATHS.login_redirect.redirect_query_param
DEFAULT_RATE_LIMIT_BUDGET = RateLimitBudget(name="requests", max_requests=600, window_seconds=60)
# The health check is the edge's readiness probe.
RATE_LIMIT_EXEMPT_PATH_RULES = PathRules(exact=frozenset({HEALTH_PATH}))

PEOPLE_PATH_RULES = PathRules(prefixes=(f"{PEOPLE_PATH}/",))

# The immutable files of the page build set their own header on success (routes.py): a rule here would also
# stamp it onto the 401, 429 and 404 that the same path can answer. Every other build file is revalidated.
NO_CACHE_PATH_RULES = PathRules(
    exact=frozenset({HOME_PATH, LOGIN_PATH, NO_ACCESS_PATH, CALL_FEATURES_PATH}),
    prefixes=(f"{STATIC_PATH}/", f"{CALL_PATH}/"),
    patterns=(re.compile(rf"^{APP_ASSET_PATH}/(?!{IMMUTABLE_ASSET_DIRECTORY}/)"),),
)
