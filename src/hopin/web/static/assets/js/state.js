export const state = {
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
