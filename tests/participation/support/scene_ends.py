"""Ways the presence of a joined agent ends, each a step on a scene (`call`, `owner`, `token`) of an agent that joined
over the call's `/mcp`."""

from agents.member import LEASE_SECONDS

from hopin.agent.tools import LEAVE_CALL


def leaves(scene):
    scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id})


def lease_ends(scene):
    scene.call.pass_time(LEASE_SECONDS)


def shuts_down(scene):
    scene.call.shut_down()
