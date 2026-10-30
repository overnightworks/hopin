import { isConnected, on, send, type ServerFrame } from "#lib/call/signaling.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { type RemoteMediaState, setRemoteMediaState } from "#lib/call/stage-port.ts";
import { state } from "#lib/call/state.ts";

function showsOwnVideo() {
    return state.screenStream !== null || state.videoEnabled;
}

export function announceMediaState() {
    if (!isConnected()) return;
    send({ type: SIGNAL_TYPE.MEDIA_STATE, audio: state.audioEnabled, video: showsOwnVideo() });
}

export function registerMediaStateHandlers() {
    on(SIGNAL_TYPE.MEDIA_STATE, (data) => {
        const { from, audio, video } = data as ServerFrame & { from: string } & RemoteMediaState;
        setRemoteMediaState(from, { audio, video });
    });
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, announceMediaState);
    on(SIGNAL_TYPE.PARTICIPANT_JOINED, announceMediaState);
}
