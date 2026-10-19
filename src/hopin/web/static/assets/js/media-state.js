import { SIGNAL_TYPE, isConnected, on, send } from "./signaling.js";
import { setRemoteMediaState } from "./stage.js";
import { state } from "./state.js";

function showsOwnVideo() {
    return state.screenStream !== null || (state.hasCamera && state.videoEnabled);
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
