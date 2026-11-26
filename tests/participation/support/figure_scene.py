"""The scene of an agent that offers a figure: the figure origin behind the transport port, the accept with or without
one, and the agent joined with a shown figure; the accept goes over the raw wire, so its figure and looks may be what a
typed client never sends."""

from agents.member import ACCEPT_INVITATION_TOOL

from hopin.agent.tools import LEAVE_CALL
from hopin.participation.vocabulary import ParticipationSignal
from tests.participation.support.figures import digest_of, glb
from tests.support.dialed_call import accept_arguments
from tests.support.signed_in import cookie_header

JOINED = ParticipationSignal.AGENT_JOINED.value
OFFERS = ParticipationSignal.AGENT_OFFERS.value
FILE = glb(b"mozart" * 8)
ROOM = glb(b"backdrop" * 8)
REFUSED_EVENT = "agent_figure.refused"


def figure_of(url, body=FILE, clips=None):
    """The accept's figure; `clips` maps a gesture to the URL and the bytes of the clip that replaces it."""
    gestures = {name: {"url": clip_url, "sha256": digest_of(clip)} for name, (clip_url, clip) in (clips or {}).items()}
    return {"url": url, "sha256": digest_of(body)} | ({"gestures": gestures} if gestures else {})


def room_of(url, body=ROOM):
    """The `{url, sha256}` a figure look names as the agent's room."""
    return {"url": url, "sha256": digest_of(body)}


def figure_look(url, body=FILE, room=None):
    """The figure look of an accept's `looks`, with its room when one is given."""
    return {"kind": "figure"} | figure_of(url, body) | ({} if room is None else {"room": room})


def accept_with(scene, figure, *, token=None, name=None, looks=None):
    """The member adds the agent and it accepts with the figure and the looks, or with none."""
    if name is None:
        scene.invite()
    else:
        scene.add(key_offered(scene, name))
        scene.member.frame_of_type(OFFERS)
    return accept_invitation(scene, figure, token=token, looks=looks)


def accept_invitation(scene, figure, *, token=None, looks=None):
    """The agent accepts the invitation it holds, with the figure and the looks, or with none."""
    token = token or scene.token
    standing = next(item for item in scene.call.standing(token) if item["state"] == "invited")
    arguments = accept_arguments(standing) | ({} if figure is None else {"figure": figure})
    arguments |= {} if looks is None else {"looks": looks}
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


def read_figure(scene, path, method="GET", **headers):
    """The file at the path, read as the member of the scene reads it."""
    cookie = {"cookie": cookie_header(scene.member_login.set_cookie_headers)}
    return scene.call.client.request(method, path, headers=cookie | headers)


def reads_and_origin_fetches(scene, figure_origin, path, source_url):
    """The contents of two reads of the path, and how often the origin was asked for the source."""
    reads = [read_figure(scene, path).content for _ in range(2)]
    return reads, figure_origin.requested.count(source_url)


def status_of_a_served_file_once_the_agent_has_left(scene, path):
    """The path is served while the agent is in the call; its status after the agent has left."""
    assert read_figure(scene, path).status_code == 200
    scene.call.call(scene.token, LEAVE_CALL, {"room": scene.owner.room_id})
    scene.member.frame_of_type(ParticipationSignal.AGENT_LEFT.value)
    return read_figure(scene, path).status_code


def refusals_in(logs, event=REFUSED_EVENT):
    return [entry for entry in logs if entry["event"] == event]
