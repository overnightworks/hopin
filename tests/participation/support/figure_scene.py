"""The scene of an agent that offers a figure: the figure origin behind the transport port, the accept with or without
one, and the agent joined with a shown figure."""

from agents.member import ACCEPT_INVITATION_TOOL

from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.figures import digest_of, glb
from tests.support.dialed_call import accept_arguments

JOINED = ParticipationSignal.AGENT_JOINED.value
OFFERS = ParticipationSignal.AGENT_OFFERS.value
FILE = glb(b"mozart" * 8)


def figure_of(url, body=FILE, clips=None):
    """The accept's figure; `clips` maps a gesture to the URL and the bytes of the clip that replaces it."""
    gestures = {name: {"url": clip_url, "sha256": digest_of(clip)} for name, (clip_url, clip) in (clips or {}).items()}
    return {"url": url, "sha256": digest_of(body)} | ({"gestures": gestures} if gestures else {})


def accept_with(scene, figure, *, token=None, name=None):
    """The member adds the agent and it accepts with the figure, or with none."""
    if name is None:
        scene.invite()
    else:
        scene.add(key_offered(scene, name))
        scene.member.frame_of_type(OFFERS)
    return accept_invitation(scene, figure, token=token)


def accept_invitation(scene, figure, *, token=None):
    """The agent accepts the invitation it holds, with the figure or with none."""
    token = token or scene.token
    standing = next(item for item in scene.call.standing(token) if item["state"] == "invited")
    arguments = accept_arguments(standing) | ({} if figure is None else {"figure": figure})
    return scene.call.call(token, ACCEPT_INVITATION_TOOL, arguments)


def key_offered(scene, name):
    """The key of the agent of that name in the first offers the member is sent that hold it."""
    while True:
        offers = scene.member.frame_of_type(OFFERS)["offers"]
        keys = [offer["agent_key"] for offer in offers if offer["name"] == name]
        if keys:
            return keys[0]


def joined_with(scene, url, body=FILE):
    """The agent accepted with the figure at the URL; the path the owner's seat was told."""
    assert not accept_with(scene, figure_of(url, body)).is_error
    return scene.owner.frame_of_type(JOINED)["figure"]


def offered_tools(scene):
    answer = scene.call.request(scene.token, "tools/list").json()
    return {tool["name"] for tool in answer["result"]["tools"]}
