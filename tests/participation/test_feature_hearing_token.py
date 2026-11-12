"""The token HopIn holds for an agent with an account of its own, exchanged inside the agent's admitted calls at the
real `/mcp` against a fake issuer."""

import logging
import threading

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS

from hopin.participation.feature import Person
from tests.participation.support.own_accounts import OWNER
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT, TOKEN_LIFETIME_SECONDS

pytestmark = pytest.mark.integration

SCOPES = ("speech-audience",)
AGENT_SUBJECT = "agent-subject"
REFUSED_EVENT = "agent_hearing_token_refused"
# The exchange expires at a whole second counted from the clock before its post, so a test stays this far from an edge.
MARGIN_SECONDS = 5


@pytest.fixture
def hears():
    return True


@pytest.fixture
def hearing_scopes():
    return SCOPES


def agent_token(call, username="agent-one"):
    """Tokens of one agent that differ by the username they carry, as two tokens of one account do."""
    return call.agent_token(owner=OWNER, subject=AGENT_SUBJECT, preferred_username=username)


def agent_calls(call, token):
    return call.request(token, "resources/list")


def exchanges(call):
    """The exchange grants the issuer received, with the issuer's answer."""
    return [
        (request, answer)
        for request, answer in call.issuer.exchanges
        if request.form["grant_type"] == TOKEN_EXCHANGE_GRANT
    ]


def held(call):
    token = call.hearing_tokens().held(Person(call.issuer.issuer, AGENT_SUBJECT))
    return None if token is None else token.get_secret_value()


class TestExchangingForAnAgent:
    def test_the_first_call_exchanges_once_for_the_configured_scopes_and_the_holder_answers_the_issued_token(
        self, adding_call
    ):
        token = agent_token(adding_call)

        response = agent_calls(adding_call, token)

        [(request, _)] = exchanges(adding_call)
        assert (response.status_code, request.form["scope"], request.form["subject_token"]) == (
            200,
            " ".join(SCOPES),
            token,
        )
        assert held(adding_call) == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[0]

    def test_a_later_call_within_the_token_life_exchanges_nothing_more(self, adding_call):
        token = agent_token(adding_call)
        agent_calls(adding_call, token)

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS - MARGIN_SECONDS)
        agent_calls(adding_call, token)

        assert len(exchanges(adding_call)) == 1

    def test_a_call_within_a_lease_of_the_expiry_exchanges_the_token_of_that_call_and_replaces_the_holding(
        self, adding_call
    ):
        agent_calls(adding_call, agent_token(adding_call))
        newest = agent_token(adding_call, username="agent-two")

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS + MARGIN_SECONDS)
        agent_calls(adding_call, newest)

        [_, (request, _)] = exchanges(adding_call)
        assert request.form["subject_token"] == newest
        assert held(adding_call) == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[1]

    def test_a_person_without_an_account_of_their_own_leads_to_no_exchange(self, adding_call):
        account, _ = adding_call.member()

        response = agent_calls(adding_call, adding_call.token(account))

        assert (response.status_code, exchanges(adding_call)) == (200, [])

    def test_the_holding_ends_at_its_expiry(self, adding_call):
        agent_calls(adding_call, agent_token(adding_call))

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - MARGIN_SECONDS)
        before_expiry = held(adding_call)
        adding_call.pass_time(MARGIN_SECONDS)

        assert (before_expiry is not None, held(adding_call)) == (True, None)

    def test_an_agent_that_never_called_has_no_holding(self, adding_call):
        assert held(adding_call) is None

    def test_a_second_call_while_an_exchange_is_in_flight_is_served_and_exchanges_nothing(self, adding_call):
        token = agent_token(adding_call)
        hold = adding_call.issuer.hold(TOKEN_EXCHANGE_GRANT)
        first = threading.Thread(target=agent_calls, args=(adding_call, token))
        first.start()
        try:
            assert hold.parked.wait(timeout=10)
            second = agent_calls(adding_call, token)
        finally:
            hold.release.set()
            first.join(timeout=10)

        assert (second.status_code, len(exchanges(adding_call))) == (200, 1)

    def test_no_exchange_touches_a_session(self, adding_call):
        agent_calls(adding_call, agent_token(adding_call))

        assert {request.form["grant_type"] for request, _ in adding_call.issuer.exchanges} == {TOKEN_EXCHANGE_GRANT}


class TestNoHearing:
    @pytest.mark.parametrize(
        ("hears", "hearing_scopes"),
        [(False, SCOPES), (True, ())],
        ids=["hearing_off", "no_scopes"],
    )
    def test_the_call_is_served_and_no_exchange_is_posted(self, adding_call):
        response = agent_calls(adding_call, agent_token(adding_call))

        assert (response.status_code, adding_call.issuer.exchanges, held(adding_call)) == (200, [], None)


class TestRefusedExchange:
    @pytest.mark.parametrize(
        ("issuer_error", "logged"),
        [("invalid_scope", "invalid_scope"), ("access_denied", "other")],
    )
    def test_a_refusal_serves_the_call_logs_one_closed_code_and_holds_nothing(self, adding_call, issuer_error, logged):
        adding_call.issuer.refuses_token_exchange_grants = True
        adding_call.issuer.token_exchange_refusal_error = issuer_error

        with structlog.testing.capture_logs() as logs:
            response = agent_calls(adding_call, agent_token(adding_call))

        [refusal] = [entry for entry in logs if entry["event"] == REFUSED_EVENT]
        assert (response.status_code, held(adding_call), refusal["reason"], refusal["idp_error"]) == (
            200,
            None,
            "token-request-refused",
            logged,
        )

    def test_an_outage_serves_the_call_and_logs_the_endpoint_failure(self, adding_call):
        adding_call.issuer.token_endpoint_is_down = True

        with structlog.testing.capture_logs() as logs:
            response = agent_calls(adding_call, agent_token(adding_call))

        [refusal] = [entry for entry in logs if entry["event"] == REFUSED_EVENT]
        assert (response.status_code, held(adding_call), refusal["reason"]) == (200, None, "token-endpoint-failed")

    def test_a_refusal_is_not_retried_within_a_lease_and_is_after_it(self, adding_call):
        adding_call.issuer.refuses_token_exchange_grants = True
        token = agent_token(adding_call)
        agent_calls(adding_call, token)
        agent_calls(adding_call, token)
        within_the_lease = len(exchanges(adding_call))

        adding_call.pass_time(LEASE_SECONDS)
        agent_calls(adding_call, token)

        assert (within_the_lease, len(exchanges(adding_call))) == (1, 2)

    def test_a_refusal_of_a_renewal_keeps_the_holding_until_it_expires(self, adding_call):
        token = agent_token(adding_call)
        agent_calls(adding_call, token)
        adding_call.issuer.refuses_token_exchange_grants = True
        kept = held(adding_call)

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS)
        agent_calls(adding_call, token)

        assert (held(adding_call), len(exchanges(adding_call))) == (kept, 2)


class TestNoTokenInALog:
    @pytest.mark.parametrize("refused", [False, True], ids=["exchanged", "refused"])
    def test_a_debug_log_of_an_exchange_carries_no_token(self, adding_call, caplog, refused):
        adding_call.issuer.refuses_token_exchange_grants = refused
        presented = agent_token(adding_call)

        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs:
            agent_calls(adding_call, presented)

        secrets = [presented, *adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)]
        assert [secret for secret in secrets if secret in caplog.text or secret in repr(logs)] == []
        assert len(exchanges(adding_call)) == 1
