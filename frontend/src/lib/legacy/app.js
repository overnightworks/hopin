import { pinLast, receiveLine, registerChatHandlers, showCard } from "#lib/ui/chat.svelte.ts";
import {
    addMoreItems,
    confirmLockChange,
    hideLockButton,
    installLeaveRequested,
    renderControls,
    showLockButton,
} from "#lib/ui/controls.svelte.ts";
import { END_KIND, endKindOfClosedRoom, endKindOfRefusal, showEnd } from "#lib/ui/end.svelte.ts";
import { hideWaitingCard, setCallLink, showWaitingCard } from "#lib/ui/invite.svelte.ts";
import { installLeaveConfirmed, showLeaveDialog } from "#lib/ui/leave-dialog.svelte.ts";
import { stopLocalMedia } from "#lib/call/media.ts";
import { registerMediaStateHandlers } from "#lib/call/media-state.ts";
import {
    loadIceServers,
    onPeersChanged,
    peerCount,
    registerMeshHandlers,
    removeAllPeers,
    SignedOutError,
} from "#lib/call/mesh.ts";
import { loadFeatures } from "#lib/call/features.ts";
import { playJoinSound, playLeaveSound } from "#lib/call/sounds.ts";
import { connect, disconnect, isConnected, on, onSessionEnded, onSessionLost, send } from "#lib/call/signaling.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { installStage, showLocalStream, showRemoteName } from "#lib/call/stage-port.ts";
import * as stageModule from "#lib/ui/stage.svelte.ts";
import { state } from "#lib/call/state.ts";
import { hideParticipantCount, setStatus, showLockIndicator, showParticipantCount } from "#lib/ui/status.svelte.ts";
import { readAccount } from "#lib/ui/account.svelte.ts";
import { installStartRequested, showGreeting } from "#lib/ui/home.svelte.ts";
import { TOAST_KIND, clearToast, showToast } from "#lib/ui/toast.svelte.ts";
import { PREJOIN_KIND, installPrejoinStart, openPrejoin } from "#lib/ui/prejoin.svelte.ts";
import { callUrl, getRoomIdFromUrl, goThroughSignIn, showCallAddress } from "#lib/ui/navigation.ts";
import { SCREEN, showScreen } from "#lib/ui/screens.svelte.ts";

installStage(stageModule);

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
// Signaling drops a frame no handler takes for good, so every feature is installed before it connects.
// A feature that fails to load never stops the call; it resolves to whether every feature is in.
// The gated feature list is asked when the first call starts, never at page load.
let featuresInstalled;

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
    featuresInstalled ??= loadFeatures(import.meta.glob("#lib/features/*/index.ts"), FEATURE_CORE);
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
        void showCallAddress(state.roomId);
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
installLeaveConfirmed(leaveCall);
installLeaveRequested(requestLeave);
installStartRequested(() => openPrejoin(PREJOIN_KIND.HOST));
void readAccount().then(showGreeting);
installPrejoinStart((kind) => {
    state.isHost = kind === PREJOIN_KIND.HOST;
    void startCall();
});

if (getRoomIdFromUrl()) {
    openPrejoin(PREJOIN_KIND.GUEST);
} else {
    showScreen(SCREEN.HOME);
}
