import asyncio
from dataclasses import dataclass

from hopin.config.constants import SignalType
from tests.support.signaling_fakes import FakeSocket, account_named, disconnect_frame, text_frame

SETTLE_TIMEOUT_SECONDS = 5

MISSING = object()


class Browser:
    """One tab in a call: its sockets, the seat credentials it was handed and the frames it numbers."""

    def __init__(self, link, name):
        self.link = link
        self.name = name
        self.sockets = []
        self.peer_id = None
        self.token = None
        self.sent_count = 0

    @property
    def socket(self):
        return self.sockets[-1]

    @property
    def received_seq(self):
        return max((frame["seq"] for socket in self.sockets for frame in socket.sent if "seq" in frame), default=0)

    def frames(self, *signal_types):
        wanted = {signal_type.value for signal_type in signal_types}
        return [frame for socket in self.sockets for frame in socket.sent if frame["type"] in wanted]

    def take_credentials(self, frame):
        self.peer_id, self.token = frame["participant_id"], frame["resume_token"]

    async def say(self, data):
        self.sent_count += 1
        await self.send_raw({**data, "seq": self.sent_count})

    async def send_raw(self, data):
        self.socket.push(text_frame(data))
        await self.link.settle(self.socket)

    async def drop(self, code=1006):
        self.socket.push(disconnect_frame(code))
        await self.link.tasks[self.socket]

    def resume_frame(self, **overrides):
        frame = {
            "type": SignalType.RESUME.value,
            "participant_id": self.peer_id,
            "token": self.token,
            "last_seq": self.received_seq,
            "sent_floor": 0,
            **overrides,
        }
        return {key: value for key, value in frame.items() if value is not MISSING}

    async def resume(self, account=None, **overrides):
        self.sockets.append(await self.link.open(account or account_named(self.name)))
        await self.send_raw(self.resume_frame(**overrides))
        return self.socket

    async def accept_resumption(self):
        [resumed] = self.frames(SignalType.RESUMED)[-1:]
        self.token = resumed["resume_token"]


class Link:
    """Runs sockets through the handler as tasks, as the web server would."""

    def __init__(self, handler):
        self.handler = handler
        self.tasks = {}

    async def open(self, account, **socket_options):
        socket = FakeSocket([], account=account, **socket_options)
        self.tasks[socket] = asyncio.create_task(self.handler.handle_websocket(socket))
        await self.settle(socket)
        return socket

    async def settle(self, socket):
        task = self.tasks[socket]
        idle = asyncio.ensure_future(socket.idle.wait())
        done, _ = await asyncio.wait({idle, task}, timeout=SETTLE_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        idle.cancel()
        if task in done:
            task.result()
        else:
            assert idle in done, "the handler neither went idle nor finished"

    async def join(self, name, room_id=None):
        browser = Browser(self, name)
        browser.sockets.append(await self.open(account_named(name)))
        await browser.send_raw({"type": SignalType.JOIN.value, "room_id": room_id})
        first_reply = browser.sockets[0].sent[0]
        if "resume_token" in first_reply:
            browser.take_credentials(first_reply)
        return browser

    async def close_all(self):
        for socket, task in self.tasks.items():
            if not task.done():
                socket.push(disconnect_frame(1000))
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


class Hold:
    """A point where the server waits until the test releases it; both waits fail on timeout."""

    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def hold(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), timeout=SETTLE_TIMEOUT_SECONDS)

    async def reached(self):
        await asyncio.wait_for(self.entered.wait(), timeout=SETTLE_TIMEOUT_SECONDS)


class HeldClose(Hold):
    """Holds the server right after it closes one socket, until the test releases it."""

    def __init__(self, socket):
        super().__init__()
        close = socket.close

        async def close_and_hold(code):
            await close(code)
            await self.hold()

        socket.close = close_and_hold


class HeldWrite(Hold):
    """Holds each write to one socket before it is sent, until the test releases it."""

    def __init__(self, socket):
        super().__init__()
        send_text = socket.send_text

        async def hold_and_send(text):
            await self.hold()
            await send_text(text)

        socket.send_text = hold_and_send


async def host_and_guest(link):
    host = await link.join("host")
    guest = await link.join("guest", host.socket.sent[0]["room_id"])
    return host, guest


@dataclass
class HeldReplay:
    reached: asyncio.Event
    release: asyncio.Event


async def resume_holding_the_first_replayed_frame(link, browser):
    held = HeldReplay(reached=asyncio.Event(), release=asyncio.Event())
    socket = await link.open(account_named(browser.name))
    write = socket.send_text

    async def hold_replayed_frames(text):
        if socket.sent:
            held.reached.set()
            await held.release.wait()
        await write(text)

    socket.send_text = hold_replayed_frames
    browser.sockets.append(socket)
    socket.push(text_frame(browser.resume_frame()))
    return held


async def fill_server_log(host, frames):
    for index in range(frames):
        await host.say({"type": SignalType.CHAT.value, "message": f"message {index}"})
