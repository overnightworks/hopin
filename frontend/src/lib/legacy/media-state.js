import { isConnected, on, send } from "#lib/call/signaling.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { setRemoteMediaState } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

function showsOwnVideo() {
    return state.screenStream !== null || state.videoEnabled;
}

export function announceMediaState() {
    if (!isConnected()) return;
    send({ type: SIGNAL_TYPE.MEDIA_STATE, audio: state.audioEnabled, video: showsOwnVideo() });
}

export function registerMediaStateHandlers() {
    on(SIGNAL_TYPE.MEDIA_STATE, (data) => setRemoteMediaState(data.from, { audio: data.audio, video: data.video }));
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, announceMediaState);
    on(SIGNAL_TYPE.PARTICIPANT_JOINED, announceMediaState);
}
