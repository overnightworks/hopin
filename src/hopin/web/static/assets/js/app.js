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
import { END_KIND, endKindOfClosedRoom, endKindOfRefusal, initEnd, showEnd } from "./end.js";
import { acquireMedia, stopLocalMedia } from "./media.js";
import { registerMediaStateHandlers } from "./media-state.js";
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
    on,
    onSessionLost,
} from "./signaling.js";
import { initPip } from "./pip.js";
import { showLocalStream, updateLocalPreview } from "./stage.js";
import { state } from "./state.js";
import {
    hideParticipantCount,
    setStatus,
    showLockIndicator,
    showParticipantCount,
} from "./status.js";
import { initHome } from "./home.js";
import { SCREEN, callUrl, getRoomIdFromUrl, showScreen } from "./router.js";

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
    return Promise.allSettled([acquireMedia(preferCamera), loadIceServers()]).then((outcomes) => {
        if (state.intentionalLeave) return;
        const failure = outcomes.find((outcome) => outcome.status === "rejected");
        if (failure) {
            stopLocalMedia();
            setStatus(failure.reason.message, "error");
            return;
        }
        showLocalStream(state.localStream);
        renderVideoButton();
        updateLocalPreview();
        connect();
    });
}

function tearDownCall() {
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
}

function leaveCall() {
    tearDownCall();
    showEnd(getRoomIdFromUrl() === null ? END_KIND.HOST_LEFT : END_KIND.LEFT);
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
        showShareLink(callUrl(state.roomId));
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
    on(SIGNAL_TYPE.ROOM_CLOSED, (data) => {
        tearDownCall();
        showEnd(endKindOfClosedRoom(data.reason), data);
    });
    on(SIGNAL_TYPE.ERROR, (data) => {
        const refusal = endKindOfRefusal(data.reason);
        if (refusal === null) {
            setStatus(`Error: ${data.message}`, "error");
            return;
        }
        tearDownCall();
        showEnd(refusal, data);
    });
}

registerMeshHandlers();
registerChatHandlers();
registerMediaStateHandlers();
registerSessionHandlers();
onPeersChanged(updateParticipantCount);
onSessionLost(removeAllPeers);
initDialogs({
    onSetupStart: startCall,
    onLeaveConfirmed: leaveCall,
});
initControls({ onLeaveRequested: requestLeave });
initChat();
initPip();
initEnd();
initHome({
    onStartRequested: () => {
        showScreen(SCREEN.CALL);
        showSetupDialog();
    },
});

if (getRoomIdFromUrl()) {
    showScreen(SCREEN.CALL);
    void startCall(true);
} else {
    showScreen(SCREEN.HOME);
}
