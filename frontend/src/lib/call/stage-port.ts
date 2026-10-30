// The one seam where ported call code reaches the stage's tile DOM. The page installs
// the stage once at start-up; callers import the forwarders, never the stage.

export const CONNECTION_QUALITY = { GOOD: "good", FAIR: "fair", POOR: "poor" } as const;

export type ConnectionQuality = (typeof CONNECTION_QUALITY)[keyof typeof CONNECTION_QUALITY];

export interface RemoteMediaState {
    audio: boolean;
    video: boolean;
}

export interface StagePort {
    showLocalStream(stream: MediaStream | null): void;
    updateLocalPreview(): void;
    addRemoteTile(participantId: string): void;
    removeRemoteTile(participantId: string): boolean;
    showRemoteStream(participantId: string, stream: MediaStream): void;
    showRemoteName(participantId: string): void;
    setRemoteMediaState(participantId: string, mediaState: RemoteMediaState): void;
    setRemoteSpeaking(participantId: string, speaking: boolean): void;
    hasTile(participantId: string): boolean;
    setConnectionQuality(participantId: string, quality: ConnectionQuality): void;
}

let installed: StagePort | null = null;

export function installStage(implementation: StagePort): void {
    if (installed) throw new Error("The stage is already installed");
    installed = implementation;
}

function installedStage(): StagePort {
    if (!installed) throw new Error("The stage is not installed");
    return installed;
}

export const showLocalStream: StagePort["showLocalStream"] = (stream) => {
    installedStage().showLocalStream(stream);
};
export const updateLocalPreview: StagePort["updateLocalPreview"] = () => {
    installedStage().updateLocalPreview();
};
export const addRemoteTile: StagePort["addRemoteTile"] = (participantId) => {
    installedStage().addRemoteTile(participantId);
};
export const removeRemoteTile: StagePort["removeRemoteTile"] = (participantId) =>
    installedStage().removeRemoteTile(participantId);
export const showRemoteStream: StagePort["showRemoteStream"] = (participantId, stream) => {
    installedStage().showRemoteStream(participantId, stream);
};
export const showRemoteName: StagePort["showRemoteName"] = (participantId) => {
    installedStage().showRemoteName(participantId);
};
export const setRemoteMediaState: StagePort["setRemoteMediaState"] = (participantId, mediaState) => {
    installedStage().setRemoteMediaState(participantId, mediaState);
};
export const setRemoteSpeaking: StagePort["setRemoteSpeaking"] = (participantId, speaking) => {
    installedStage().setRemoteSpeaking(participantId, speaking);
};
export const hasTile: StagePort["hasTile"] = (participantId) => installedStage().hasTile(participantId);
export const setConnectionQuality: StagePort["setConnectionQuality"] = (participantId, quality) => {
    installedStage().setConnectionQuality(participantId, quality);
};
