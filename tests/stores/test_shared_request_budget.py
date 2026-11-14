from http import HTTPStatus

import pytest
from webauth.policies import RateLimitPolicy

from hopin.config.constants import AUTH_START_PATH, HEALTH_PATH, LOGIN_PATH

pytestmark = pytest.mark.shared_stores

# HopIn takes webauth's default budgets, so this is the library's number for a sign-in start.
SIGN_IN_BUDGET_REQUESTS = RateLimitPolicy().sign_in.max_requests


class TestSharedRequestBudget:
    def test_a_budget_spent_through_one_process_is_refused_through_the_other(self, pods):
        first, second = pods

        spent = [first.status(AUTH_START_PATH) for _ in range(SIGN_IN_BUDGET_REQUESTS)]

        assert set(spent) == {HTTPStatus.SEE_OTHER}
        assert second.status(AUTH_START_PATH) == HTTPStatus.TOO_MANY_REQUESTS
        assert first.status(AUTH_START_PATH) == HTTPStatus.TOO_MANY_REQUESTS

    def test_with_redis_stopped_both_processes_are_not_ready_and_a_budgeted_route_answers_503(self, pods, redis_gate):
        redis_gate.close()

        assert [pod.status(HEALTH_PATH) for pod in pods] == [HTTPStatus.SERVICE_UNAVAILABLE] * 2
        assert [pod.status(LOGIN_PATH) for pod in pods] == [HTTPStatus.SERVICE_UNAVAILABLE] * 2
