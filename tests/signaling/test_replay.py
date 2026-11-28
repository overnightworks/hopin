"""What a frame supersedes in a seat's log."""

import pytest

from hopin.signaling.replay import Supersedes


def test_a_supersede_that_names_another_key_than_the_agents_fails_loud():
    with pytest.raises(ValueError, match="agent_id"):
        Supersedes(frozenset({"agent_state"}), holding=("name", "one"))
