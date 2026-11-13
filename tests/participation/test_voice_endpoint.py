"""The voice route reads the seats and the sounds, which the event loop changes, so it runs on that loop."""

import threading
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from hopin.participation import voice
from hopin.participation.voice import VoiceStore, voice_endpoint


def test_the_seat_check_of_the_voice_route_runs_on_the_thread_of_the_event_loop(monkeypatch):
    monkeypatch.setattr(voice, "admitted_user", lambda request: SimpleNamespace(id="account"))
    store = VoiceStore()
    stored = store.put("room", "speaker", bytes(10))
    seat_check_threads: list[int] = []
    loop_threads: list[int] = []

    def holds_seat(account: str, room: str) -> bool:
        seat_check_threads.append(threading.get_ident())
        return True

    async def on_the_loop(request: Request) -> None:
        loop_threads.append(threading.get_ident())

    app = FastAPI()
    app.add_api_route("/loop", on_the_loop)
    app.add_api_route("/voice/{voice_id}", voice_endpoint(store, holds_seat))

    with TestClient(app) as client:
        client.get("/loop")
        answer = client.get(f"/voice/{stored.id}")

    assert answer.status_code == 200
    assert seat_check_threads == loop_threads
