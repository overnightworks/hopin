import { initAccount } from "./account.js";
import { initChat, pinLast, receiveLine, registerChatHandlers, showCard } from "./chat.js";
import {
    addMoreItems,
    confirmLockChange,
    hideLockButton,
    initControls,
    renderControls,
    showLockButton,
} from "./controls.js";
import { initDialogs, showLeaveDialog } from "./dialogs.js";
import { END_KIND, endKindOfClosedRoom, endKindOfRefusal, initEnd, showEnd } from "./end.js";
import { hideWaitingCard, initInvite, setCallLink, showWaitingCard } from "./invite.js";
import { initMenus } from "./menus.js";
import { stopLocalMedia } from "./media.js";
import { registerMediaStateHandlers } from "./media-state.js";
import {
    loadIceServers,
    onPeersChanged,
    peerCount,
    registerMeshHandlers,
    removeAllPeers,
    SignedOutError,
} from "./mesh.js";
import { playJoinSound, playLeaveSound } from "./sounds.js";
import { SIGNAL_TYPE, connect, disconnect, isConnected, on, onSessionEnded, onSessionLost, send } from "./signaling.js";
import { initPip } from "./pip.js";
import { showLocalStream, showRemoteName } from "./stage.js";
import { state } from "./state.js";
import { hideParticipantCount, setStatus, showLockIndicator, showParticipantCount } from "./status.js";
import { initHome, showGreeting } from "./home.js";
import { TOAST_KIND, clearToast, showToast } from "./toast.js";
import { PREJOIN_KIND, initPrejoin, openPrejoin } from "./prejoin.js";
import { SCREEN, callUrl, getRoomIdFromUrl, goThroughSignIn, showCallAddress, showScreen } from "./router.js";

const callEndedHooks = [];
// What a feature's browser module may reach; its `install` gets it before signaling connects.
const FEATURE_CORE = Object.freeze({
    on,
    send,
    isConnected,
    showToast,
    toastKind: TOAST_KIND,
    addMoreItems,
    receiveChatLine: receiveLine,
    pinLastChatLine: pinLast,
    showChatCard: showCard,
    onCallEnded: (hook) => {
        callEndedHooks.push(hook);
    },
});
// The served vocabulary loads before the page starts, as a static import would; the bundle does not own /static/js/wire.js.
const WIRE_URL = "/static/js/wire.js";
const { FEATURE_MODULES } = await import(/* @vite-ignore */ WIRE_URL);
// Signaling drops a frame no handler takes for good, so every feature is installed before it connects.
// A feature that fails to load never stops the call; it resolves to whether every feature is in.
const featuresInstalled = Promise.all(
    FEATURE_MODULES.map(async (moduleUrl) => (await import(/* @vite-ignore */ moduleUrl)).install(FEATURE_CORE)),
).then(
    () => true,
    () => false,
);

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
    return Promise.all([loadIceServers(), featuresInstalled]).then(
        ([, everyFeatureInstalled]) => {
            if (state.intentionalLeave) return;
            if (!everyFeatureInstalled) showToast("An add-on could not load.", TOAST_KIND.WARNING);
            connect();
        },
        (error_) => {
            if (state.intentionalLeave) return;
            if (error_ instanceof SignedOutError) {
                goThroughSignIn();
                return;
            }
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
    clearToast();
    for (const hook of callEndedHooks) hook();
    hideWaitingCard();
    hideLockButton();
    hideParticipantCount();
    state.isHost = false;
    state.isLocked = false;
    state.roomId = null;
    state.namesByParticipant.clear();
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
        for (const [participantId, name] of Object.entries(data.names)) {
            state.namesByParticipant.set(participantId, name);
            showRemoteName(participantId);
        }
        state.isHost = data.is_host;
        state.isLocked = data.locked;
        state.reconnectAttempt = 0;
        setCallLink(callUrl(getRoomIdFromUrl()));
        setStatus("In call", "connected");
        updateLockUI();
        updateParticipantCount();
    });
    on(SIGNAL_TYPE.PARTICIPANT_JOINED, (data) => {
        state.namesByParticipant.set(data.participant_id, data.name);
        playJoinSound();
        updateParticipantCount();
    });
    on(SIGNAL_TYPE.PARTICIPANT_LEFT, playLeaveSound);
    on(SIGNAL_TYPE.ROOM_LOCKED, () => {
        state.isLocked = true;
        updateLockUI();
        confirmLockChange(true);
    });
    on(SIGNAL_TYPE.ROOM_UNLOCKED, () => {
        state.isLocked = false;
        updateLockUI();
        confirmLockChange(false);
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
onSessionEnded(() => {
    tearDownCall();
    goThroughSignIn();
});
initDialogs({ onLeaveConfirmed: leaveCall });
initMenus();
initControls({ onLeaveRequested: requestLeave });
initInvite();
initChat();
initPip();
initEnd();
initHome({ onStartRequested: () => openPrejoin(PREJOIN_KIND.HOST) });
initAccount({ onNameRead: showGreeting });
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
