import pytest

from hopin.participation.live_agents import LiveAgents, TimerAction
from hopin.participation.participants import Person

LEASE = 30.0
PERSON = Person("issuer", "agent-subject")


def admitted(agents, *, now, owner="owner", name="Claude", person=PERSON):
    key = f"key-{agents.registrations + 1}"
    return agents.admit(person, owner, name, now=now, lease_seconds=LEASE, new_key=lambda: key)


class TestAdmit:
    def test_a_first_call_registers_the_agent_with_a_lease(self):
        admission = admitted(LiveAgents(), now=10.0)

        assert (admission.registered, admission.ended, admission.agent.deadline) == (True, None, 10.0 + LEASE)

    def test_a_call_within_the_lease_renews_it_and_keeps_the_registration(self):
        first = admitted(LiveAgents(), now=0.0)

        again = admitted(first.agents, now=LEASE - 1)

        assert (again.registered, again.ended, again.agent.deadline) == (False, None, LEASE - 1 + LEASE)
        assert (again.agent.key, again.agent.generation) == (first.agent.key, first.agent.generation)

    def test_a_call_after_the_deadline_ends_the_old_registration_before_its_timer_ran(self):
        first = admitted(LiveAgents(), now=0.0)

        later = admitted(first.agents, now=LEASE)

        assert (later.registered, later.ended) == (True, first.agent)
        assert later.agent.generation != first.agent.generation
        assert later.agent.key != first.agent.key

    def test_a_call_of_another_owner_ends_the_old_registration(self):
        first = admitted(LiveAgents(), now=0.0)

        changed = admitted(first.agents, now=1.0, owner="other owner")

        assert (changed.registered, changed.ended, changed.agent.owner_subject) == (True, first.agent, "other owner")

    def test_a_new_name_renews_the_lease_and_ends_nothing(self):
        first = admitted(LiveAgents(), now=0.0)

        renamed = admitted(first.agents, now=1.0, name="Other")

        assert (renamed.registered, renamed.ended, renamed.agent.name) == (False, None, "Other")

    def test_each_person_holds_a_lease_of_its_own(self):
        first = admitted(LiveAgents(), now=0.0)

        other = admitted(first.agents, now=0.0, person=Person("issuer", "other-agent"))

        assert (other.registered, other.ended, len(other.agents.by_person)) == (True, None, 2)


class TestChecked:
    def test_a_timer_of_a_gone_agent_does_nothing(self):
        assert LiveAgents().checked(PERSON, 1, now=0.0).action == TimerAction.NOTHING

    def test_a_timer_of_a_replaced_registration_does_nothing(self):
        first = admitted(LiveAgents(), now=0.0)
        replaced = admitted(first.agents, now=1.0, owner="other owner")

        check = replaced.agents.checked(PERSON, first.agent.generation, now=LEASE + 5)

        assert (check.action, check.agents) == (TimerAction.NOTHING, replaced.agents)

    def test_a_timer_before_the_deadline_rearms_for_the_rest_of_the_lease(self):
        first = admitted(LiveAgents(), now=0.0)
        renewed = admitted(first.agents, now=LEASE - 1)

        check = renewed.agents.checked(PERSON, first.agent.generation, now=LEASE)

        assert (check.action, check.rearm_after) == (TimerAction.REARM, pytest.approx(LEASE - 1))

    def test_a_timer_at_the_deadline_expires_the_agent(self):
        first = admitted(LiveAgents(), now=0.0)

        check = first.agents.checked(PERSON, first.agent.generation, now=LEASE)

        assert (check.action, check.ended, check.agents.by_person) == (TimerAction.EXPIRE, first.agent, {})
