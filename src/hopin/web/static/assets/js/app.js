import { initChat, registerChatHandlers } from "./chat.js";
import {
    hideLockButton,
    hideShareBox,
    initControls,
    renderVideoButton,
    showLockButton,
    showShareLink,
} from "./controls.js";
import { initDialogs, showLeaveDialog, showSetupDialog } from "./dialogs.js";
import { acquireMedia, stopLocalMedia } from "./media.js";
import {
    loadIceServers,
    onPeersChanged,
    peerCount,
    registerMeshHandlers,
    removeAllPeers,
} from "./mesh.js";
import { playJoinSound, playLeaveSound } from "./sounds.js";
import {
    SIGNAL_TYPE,
    connect,
    disconnect,
    getRoomIdFromUrl,
    on,
    onBeforeReconnect,
} from "./signaling.js";
import { showLocalStream, updateLocalPreview } from "./stage.js";
import { state } from "./state.js";
import {
    hideParticipantCount,
    setStatus,
    showLockIndicator,
    showParticipantCount,
} from "./status.js";

function updateParticipantCount() {
    if (state.roomId) {
        showParticipantCount(peerCount() + 1);
    } else {
        hideParticipantCount();
    }
}

function updateLockUI() {
    if (state.isHost) showLockButton(state.isLocked);
    showLockIndicator(state.isLocked);
}

function startCall(preferCamera) {
    state.intentionalLeave = false;
    state.reconnectAttempt = 0;
    return Promise.all([acquireMedia(preferCamera), loadIceServers()])
        .then(() => {
            showLocalStream(state.localStream);
            renderVideoButton();
            updateLocalPreview();
            connect();
        })
        .catch((error) => {
            setStatus(error.message, "error");
        });
}

function leaveCall() {
    state.intentionalLeave = true;
    removeAllPeers();
    stopLocalMedia();
    disconnect();
    showLocalStream(null);
    hideShareBox();
    hideLockButton();
    hideParticipantCount();
    state.isHost = false;
    state.isLocked = false;
    state.roomId = null;
    updateLockUI();
    setStatus("Left call", "disconnected");
}

function requestLeave() {
    if (state.roomId) {
        showLeaveDialog();
    } else {
        leaveCall();
    }
}

function registerSessionHandlers() {
    on(SIGNAL_TYPE.ROOM_CREATED, (data) => {
        state.roomId = data.room_id;
        state.isHost = data.is_host;
        state.reconnectAttempt = 0;
        showShareLink(`${location.origin}/join/${state.roomId}`);
        setStatus("In call", "connected");
        updateLockUI();
        updateParticipantCount();
    });
    on(SIGNAL_TYPE.EXISTING_PARTICIPANTS, (data) => {
        state.isHost = data.is_host;
        state.isLocked = data.locked;
        state.reconnectAttempt = 0;
        setStatus("In call", "connected");
        updateLockUI();
        updateParticipantCount();
    });
    on(SIGNAL_TYPE.PARTICIPANT_JOINED, () => {
        playJoinSound();
        updateParticipantCount();
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, playLeaveSound);
    on(SIGNAL_TYPE.ROOM_LOCKED, () => {
        state.isLocked = true;
        updateLockUI();
    });
    on(SIGNAL_TYPE.ROOM_UNLOCKED, () => {
        state.isLocked = false;
        updateLockUI();
    });
    on(SIGNAL_TYPE.ROOM_CLOSED, () => {
        leaveCall();
        setStatus("Room closed", "disconnected");
    });
    on(SIGNAL_TYPE.ERROR, (data) => {
        setStatus(`Error: ${data.message}`, "error");
    });
}

registerMeshHandlers();
registerChatHandlers();
registerSessionHandlers();
onPeersChanged(updateParticipantCount);
onBeforeReconnect(removeAllPeers);
initDialogs({
    onSetupStart: startCall,
    onLeaveConfirmed: leaveCall,
});
initControls({ onLeaveRequested: requestLeave });
initChat();

if (getRoomIdFromUrl()) {
    void startCall(true);
} else {
    showSetupDialog();
}
