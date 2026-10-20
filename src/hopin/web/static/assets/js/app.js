import { initChat, registerChatHandlers } from "./chat.js";
import { hideLockButton, initControls, renderControls, showLockButton } from "./controls.js";
import { initDialogs, showLeaveDialog } from "./dialogs.js";
import { END_KIND, endKindOfClosedRoom, endKindOfRefusal, initEnd, showEnd } from "./end.js";
import { hideWaitingCard, initInvite, setCallLink, showWaitingCard } from "./invite.js";
import { stopLocalMedia } from "./media.js";
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
import { showLocalStream } from "./stage.js";
import { state } from "./state.js";
import {
    hideParticipantCount,
    setStatus,
    showLockIndicator,
    showParticipantCount,
} from "./status.js";
import { initHome } from "./home.js";
import { PREJOIN_KIND, initPrejoin, openPrejoin } from "./prejoin.js";
import { SCREEN, callUrl, getRoomIdFromUrl, showCallAddress, showScreen } from "./router.js";

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

function startCall() {
    renderControls();
    showScreen(SCREEN.CALL);
    state.intentionalLeave = false;
    state.reconnectAttempt = 0;
    return loadIceServers().then(
        () => {
            if (!state.intentionalLeave) connect();
        },
        (error_) => {
            if (state.intentionalLeave) return;
            stopLocalMedia();
            renderControls();
            setStatus(error_.message, "error");
        },
    );
}

function tearDownCall() {
    state.intentionalLeave = true;
    removeAllPeers();
    stopLocalMedia();
    disconnect();
    showLocalStream(null);
    hideWaitingCard();
    hideLockButton();
    hideParticipantCount();
    state.isHost = false;
    state.isLocked = false;
    state.roomId = null;
    updateLockUI();
}

function leaveCall() {
    const wasHost = state.isHost;
    tearDownCall();
    showEnd(wasHost ? END_KIND.HOST_LEFT : END_KIND.LEFT);
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
        showCallAddress(state.roomId);
        setCallLink(callUrl(state.roomId));
        showWaitingCard();
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
initDialogs({ onLeaveConfirmed: leaveCall });
initControls({ onLeaveRequested: requestLeave });
initInvite();
initChat();
initPip();
initEnd();
initHome({ onStartRequested: () => openPrejoin(PREJOIN_KIND.HOST) });
initPrejoin({
    onStart: (kind) => {
        state.isHost = kind === PREJOIN_KIND.HOST;
        void startCall();
    },
});

if (getRoomIdFromUrl()) {
    openPrejoin(PREJOIN_KIND.GUEST);
} else {
    showScreen(SCREEN.HOME);
}
