"""An agent's tool call on a place of the scene, from the agent's side of `/mcp`."""

from agents.member import PlaceRef

from hopin.agent.tools import CALL_KIND


def call_in(scene, room_id=None, *, tool, **named):
    """The agent's call of `tool` on the call `room_id`, the owner's call by default, with the arguments `named`."""
    place = PlaceRef(kind=CALL_KIND, id=scene.owner.room_id if room_id is None else room_id)
    return scene.call.call(scene.token, tool, {"place": place.model_dump(), **named})
