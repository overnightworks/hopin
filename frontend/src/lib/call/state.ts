export interface CallState {
    roomId: string | null;
    isHost: boolean;
    isLocked: boolean;
    reconnectAttempt: number;
    intentionalLeave: boolean;
    localStream: MediaStream | null;
    screenStream: MediaStream | null;
    audioEnabled: boolean;
    videoEnabled: boolean;
    namesByParticipant: Map<string, string>;
}

export const state: CallState = {
    roomId: null,
    isHost: false,
    isLocked: false,
    reconnectAttempt: 0,
    intentionalLeave: false,
    localStream: null,
    screenStream: null,
    audioEnabled: true,
    videoEnabled: true,
    namesByParticipant: new Map(),
};
