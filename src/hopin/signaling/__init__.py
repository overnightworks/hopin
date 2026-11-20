"""The call's WebSocket: who a socket is, the frames, seats, replay, timers, and the port for the call's optional
features (`features.py`).

Frames are JSON over the WebSocket. The core's signal types are `SignalType` in `config/constants.py`; a feature adds
its own through `TransitionFeature.signal_types`. A feature's frames to the browser are the records of its own
`frames.py`, which `scripts/generate_wire.py` renders as `<Feature>FrameByType`, beside the feature's enums, in the
`wire.ts` next to its browser module. A field with no default is always sent, and `T | None` renders as `T | null`, so
an empty table sends an explicit null. `FeatureCore.on` takes that map, so a handler's frame type follows the signal it
registers for. The call core takes its tokens from the generated `frontend/src/lib/wire.ts`.

`handler.py` decodes and dispatches the frames, `routing.py` owns rooms, host grace and fan-out, and `seats.py` owns
seats, delivery, parking, resume and replay.
"""
