"""The token HopIn holds for an agent with an account of its own, exchanged inside the agent's admitted calls at the
real `/mcp` against a fake issuer."""

import logging
import threading

import pytest
import structlog.testing
from agents.member import LEASE_SECONDS
from speech_client.wire import VoiceId

from hopin.agent.speech_settings import SpeechService
from hopin.participation.participants import Person
from tests.participation.support.own_accounts import OWNER
from tests.support.fake_issuer import TOKEN_EXCHANGE_GRANT, TOKEN_LIFETIME_SECONDS

pytestmark = pytest.mark.integration


class Agent:
    """An agent with an account of its own, as the call and the issuer see it."""

    def __init__(self, call, subject):
        self._call = call
        self.subject = subject

    def token(self, username="agent-one"):
        """Tokens of one agent that differ by the username they carry, as two tokens of one account do."""
        return self._call.agent_token(owner=OWNER, subject=self.subject, preferred_username=username)

    def calls(self, token):
        return self._call.request(token, "resources/list")

    def held(self):
        token = self._call.hearing_tokens().held(Person(self._call.issuer.issuer, self.subject))
        return None if token is None else token.get_secret_value()


AUDIENCE_SCOPE = "speech-audience"


@pytest.fixture
def hearing_service():
    return SpeechService("http://speech.invalid:8090", VoiceId.THORSTEN, AUDIENCE_SCOPE)


@pytest.fixture
def agent_named(adding_call):
    return lambda subject: Agent(adding_call, subject)


@pytest.fixture
def agent(agent_named):
    return agent_named("agent-subject")


@pytest.fixture
def margin_seconds():
    """The exchange expires at a whole second counted from the clock before its post, so a test stays this far from an
    edge."""
    return 5


def exchanges(call):
    """The exchange grants the issuer received, with the issuer's answer."""
    return [
        (request, answer)
        for request, answer in call.issuer.exchanges
        if request.form["grant_type"] == TOKEN_EXCHANGE_GRANT
    ]


class TestExchangingForAnAgent:
    def test_the_first_call_exchanges_once_for_the_configured_scopes_and_the_holder_answers_the_issued_token(
        self, adding_call, agent
    ):
        token = agent.token()

        response = agent.calls(token)

        [(request, _)] = exchanges(adding_call)
        assert (response.status_code, request.form["scope"], request.form["subject_token"]) == (
            200,
            AUDIENCE_SCOPE,
            token,
        )
        assert agent.held() == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[0]

    def test_a_later_call_within_the_token_life_exchanges_nothing_more(self, adding_call, agent, margin_seconds):
        token = agent.token()
        agent.calls(token)

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS - margin_seconds)
        agent.calls(token)

        assert len(exchanges(adding_call)) == 1

    def test_a_call_within_a_lease_of_the_expiry_exchanges_the_token_of_that_call_and_replaces_the_holding(
        self, adding_call, agent, margin_seconds
    ):
        agent.calls(agent.token())
        newest = agent.token(username="agent-two")

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS + margin_seconds)
        agent.calls(newest)

        [_, (request, _)] = exchanges(adding_call)
        assert request.form["subject_token"] == newest
        assert agent.held() == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[1]

    def test_the_replaced_holding_outlives_the_expiry_of_the_token_it_replaced(
        self, adding_call, agent, margin_seconds
    ):
        agent.calls(agent.token())
        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS + margin_seconds)
        agent.calls(agent.token(username="agent-two"))

        adding_call.pass_time(LEASE_SECONDS)

        assert agent.held() == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[1]

    def test_a_release_that_started_before_its_holding_was_replaced_leaves_the_replacement(
        self, adding_call, agent, margin_seconds
    ):
        agent.calls(agent.token())
        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS + margin_seconds)
        agent.calls(agent.token(username="agent-two"))

        adding_call.client.portal.call(adding_call.scheduler.run_cancelled)

        assert agent.held() == adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)[1]

    def test_a_person_without_an_account_of_their_own_leads_to_no_exchange(self, adding_call):
        account, _ = adding_call.member()

        response = adding_call.request(adding_call.token(account), "resources/list")

        assert (response.status_code, exchanges(adding_call)) == (200, [])

    def test_an_idle_holding_is_released_at_its_expiry_without_another_call(self, adding_call, agent, margin_seconds):
        agent.calls(agent.token())

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - margin_seconds)
        before_expiry = agent.held()
        adding_call.pass_time(margin_seconds)

        assert (before_expiry is not None, agent.held()) == (True, None)

    def test_a_read_after_the_expiry_answers_nothing_while_the_release_is_still_pending(
        self, adding_call, agent, margin_seconds
    ):
        agent.calls(agent.token())

        adding_call.scheduler.skip(TOKEN_LIFETIME_SECONDS + margin_seconds)

        assert agent.held() is None

    def test_an_agent_that_never_called_has_no_holding(self, agent):
        assert agent.held() is None

    def test_each_agent_holds_only_the_token_exchanged_for_its_own_call(self, adding_call, agent_named):
        first, second = agent_named("agent-first"), agent_named("agent-second")
        first_token, second_token = first.token(), second.token()

        first.calls(first_token)
        second.calls(second_token)

        presented = [request.form["subject_token"] for request, _ in exchanges(adding_call)]
        issued = adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)
        assert (presented, first.held(), second.held()) == ([first_token, second_token], issued[0], issued[1])
        assert first.held() != second.held()

    def test_a_second_call_while_an_exchange_is_in_flight_is_served_and_exchanges_nothing(self, adding_call, agent):
        token = agent.token()
        hold = adding_call.issuer.hold(TOKEN_EXCHANGE_GRANT)
        first = threading.Thread(target=agent.calls, args=(token,))
        first.start()
        try:
            assert hold.parked.wait(timeout=10)
            second = agent.calls(token)
        finally:
            hold.release.set()
            first.join(timeout=10)

        assert (second.status_code, len(exchanges(adding_call))) == (200, 1)

    def test_no_exchange_touches_a_session(self, adding_call, agent):
        agent.calls(agent.token())

        assert {request.form["grant_type"] for request, _ in adding_call.issuer.exchanges} == {TOKEN_EXCHANGE_GRANT}


class TestNoHearing:
    @pytest.fixture
    def hearing_service(self):
        return None

    def test_the_call_is_served_and_no_exchange_is_posted(self, adding_call, agent):
        response = agent.calls(agent.token())

        assert (response.status_code, adding_call.issuer.exchanges, agent.held()) == (200, [], None)


def refusals(logs):
    return [entry for entry in logs if entry["event"] == "agent_hearing_token_refused"]


class TestRefusedExchange:
    @pytest.mark.parametrize(
        ("issuer_error", "logged"),
        [("invalid_scope", "invalid_scope"), ("access_denied", "other")],
    )
    def test_a_refusal_serves_the_call_logs_one_closed_code_and_holds_nothing(
        self, adding_call, agent, issuer_error, logged
    ):
        adding_call.issuer.refuses_token_exchange_grants = True
        adding_call.issuer.token_exchange_refusal_error = issuer_error

        with structlog.testing.capture_logs() as logs:
            response = agent.calls(agent.token())

        [refusal] = refusals(logs)
        assert (response.status_code, agent.held(), refusal["reason"], refusal["idp_error"]) == (
            200,
            None,
            "token-request-refused",
            logged,
        )

    def test_an_outage_serves_the_call_and_logs_the_endpoint_failure(self, adding_call, agent):
        adding_call.issuer.token_endpoint_is_down = True

        with structlog.testing.capture_logs() as logs:
            response = agent.calls(agent.token())

        [refusal] = refusals(logs)
        assert (response.status_code, agent.held(), refusal["reason"]) == (200, None, "token-endpoint-failed")

    def test_a_refusal_is_not_retried_within_a_lease_and_is_after_it(self, adding_call, agent):
        adding_call.issuer.refuses_token_exchange_grants = True
        token = agent.token()
        agent.calls(token)
        agent.calls(token)
        within_the_lease = len(exchanges(adding_call))

        adding_call.pass_time(LEASE_SECONDS)
        agent.calls(token)

        assert (within_the_lease, len(exchanges(adding_call))) == (1, 2)

    def test_a_refusal_of_a_renewal_keeps_the_holding_until_it_expires(self, adding_call, agent):
        token = agent.token()
        agent.calls(token)
        adding_call.issuer.refuses_token_exchange_grants = True
        kept = agent.held()

        adding_call.pass_time(TOKEN_LIFETIME_SECONDS - LEASE_SECONDS)
        agent.calls(token)

        assert (agent.held(), len(exchanges(adding_call))) == (kept, 2)


class TestNoTokenInALog:
    @pytest.mark.parametrize("refused", [False, True], ids=["exchanged", "refused"])
    def test_a_debug_log_of_an_exchange_carries_no_token(self, adding_call, agent, caplog, refused):
        adding_call.issuer.refuses_token_exchange_grants = refused
        presented = agent.token()

        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as logs:
            agent.calls(presented)

        secrets = [presented, *adding_call.issuer.access_tokens_issued(TOKEN_EXCHANGE_GRANT)]
        assert [secret for secret in secrets if secret in caplog.text or secret in repr(logs)] == []
        assert len(exchanges(adding_call)) == 1
