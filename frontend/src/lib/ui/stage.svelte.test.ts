// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import { AGENT_FIGURE, type AgentFigure } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";

// The modules hold their state, so each case loads fresh copies.
async function freshStage() {
    vi.resetModules();
    const { state } = await import("#lib/call/state.ts");
    return { state, ...(await import("#lib/ui/stage.svelte.ts")) };
}

const someStream = () => new EventTarget() as MediaStream;

describe("own picture", () => {
    it("starts as the layout's markup shows it", async () => {
        const { ownPicture } = await freshStage();

        expect(ownPicture).toEqual({ stream: null, delivery: 0, mirrored: true, showsVideo: true, muted: false });
    });

    it("holds the stream and mirrors it when no screen is shared", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        const stream = someStream();

        showLocalStream(stream);

        expect(ownPicture.stream).toBe(stream);
        expect(ownPicture.mirrored).toBe(true);
    });

    it("does not mirror the stream while a screen is shared", async () => {
        const { ownPicture, showLocalStream, state } = await freshStage();
        state.screenStream = someStream();

        showLocalStream(someStream());

        expect(ownPicture.mirrored).toBe(false);
    });

    it("keeps the mirror it set until the next stream arrives", async () => {
        const { ownPicture, showLocalStream, state } = await freshStage();
        showLocalStream(someStream());

        state.screenStream = someStream();

        expect(ownPicture.mirrored).toBe(true);
    });

    it("counts a delivery for the same stream again", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        const stream = someStream();

        showLocalStream(stream);
        const first = ownPicture.delivery;
        showLocalStream(stream);

        expect(ownPicture.delivery).toBeGreaterThan(first);
    });

    it("clears the stream and still counts a delivery when the call is torn down", async () => {
        const { ownPicture, showLocalStream } = await freshStage();
        showLocalStream(someStream());
        const before = ownPicture.delivery;

        showLocalStream(null);

        expect(ownPicture.stream).toBeNull();
        expect(ownPicture.delivery).toBeGreaterThan(before);
    });

    it.each([true, false])("shows the video as the camera state %s says", async (videoEnabled) => {
        const { ownPicture, state, updateLocalPreview } = await freshStage();
        state.videoEnabled = videoEnabled;

        updateLocalPreview();

        expect(ownPicture.showsVideo).toBe(videoEnabled);
    });

    it.each([true, false])("marks the picture muted when the microphone state is %s", async (audioEnabled) => {
        const { ownPicture, state, updateLocalPreview } = await freshStage();
        state.audioEnabled = audioEnabled;

        updateLocalPreview();

        expect(ownPicture.muted).toBe(!audioEnabled);
    });
});

function fakeTrack(kind: "audio" | "video", { muted = false, readyState = "live" } = {}) {
    return Object.assign(new EventTarget(), { kind, muted, readyState }) as unknown as MediaStreamTrack;
}

function streamOf(...tracks: MediaStreamTrack[]) {
    return {
        getTracks: () => tracks,
        getVideoTracks: () => tracks.filter((track) => track.kind === "video"),
    } as unknown as MediaStream;
}

function videoShowing(stream: MediaStream | null, readyState: number) {
    const video = document.createElement("video");
    Object.defineProperty(video, "srcObject", { value: stream });
    Object.defineProperty(video, "readyState", { value: readyState });
    return video;
}

describe("remote tiles", () => {
    it("appends tiles in join order and removes one from the middle", async () => {
        const { addRemoteTile, remoteTiles, removeRemoteTile } = await freshStage();

        for (const id of ["b", "a", "c"]) addRemoteTile(id);
        removeRemoteTile("a");

        expect(remoteTiles.map((tile) => tile.id)).toEqual(["b", "c"]);
    });

    it("reports whether a tile was removed", async () => {
        const { addRemoteTile, removeRemoteTile } = await freshStage();
        addRemoteTile("a");

        expect([removeRemoteTile("a"), removeRemoteTile("a")]).toEqual([true, false]);
    });

    it("takes the name at add time and again when the name is shown", async () => {
        const { addRemoteTile, remoteTiles, showRemoteName, state } = await freshStage();
        state.namesByParticipant.set("a", "Ada");
        addRemoteTile("a");
        state.namesByParticipant.set("a", "Ada L.");

        const snapshot = remoteTiles[0]?.name;
        showRemoteName("a");

        expect([snapshot, remoteTiles[0]?.name]).toEqual(["Ada", "Ada L."]);
    });

    it("leaves the name empty for a person without one and ignores a name for a missing tile", async () => {
        const { addRemoteTile, remoteTiles, showRemoteName } = await freshStage();
        addRemoteTile("a");

        showRemoteName("a");
        showRemoteName("gone");

        expect(remoteTiles[0]?.name).toBe("");
    });

    it("stores a stream and counts a delivery only for a person with a tile", async () => {
        const { addRemoteTile, remoteStreamOf, remoteTiles, showRemoteStream } = await freshStage();
        const stream = streamOf();
        showRemoteStream("a", stream);
        addRemoteTile("a");

        const before = remoteTiles[0]?.streamDelivery ?? 0;
        showRemoteStream("a", stream);

        expect([remoteStreamOf("a"), (remoteTiles[0]?.streamDelivery ?? 0) - before]).toEqual([stream, 1]);
        showRemoteStream("b", stream);
        expect(remoteStreamOf("b")).toBeUndefined();
    });

    it("drops the stream and the quality with the tile", async () => {
        const {
            addRemoteTile,
            remoteQualityOf,
            remoteStreamOf,
            removeRemoteTile,
            setConnectionQuality,
            showRemoteStream,
        } = await freshStage();
        addRemoteTile("a");
        showRemoteStream("a", streamOf());
        setConnectionQuality("a", "good");

        removeRemoteTile("a");

        expect([remoteStreamOf("a"), remoteQualityOf("a")]).toEqual([undefined, undefined]);
    });

    it("counts a quality delivery for the same quality again, and ignores a missing tile", async () => {
        const { addRemoteTile, remoteQualityOf, remoteTiles, setConnectionQuality } = await freshStage();
        addRemoteTile("a");

        setConnectionQuality("a", "fair");
        setConnectionQuality("a", "fair");
        setConnectionQuality("gone", "poor");

        expect([remoteQualityOf("a"), remoteTiles[0]?.qualityDelivery, remoteQualityOf("gone")]).toEqual([
            "fair",
            2,
            undefined,
        ]);
    });

    it("applies a media state that arrived before its tile", async () => {
        const { addRemoteTile, remoteTiles, setRemoteMediaState } = await freshStage();
        setRemoteMediaState("a", { audio: false, video: false });

        addRemoteTile("a");

        expect(remoteTiles[0]?.isMuted).toBe(true);
    });

    it("forgets the media state and the speaking of a removed person, tile or not", async () => {
        const { addRemoteTile, remoteTiles, removeRemoteTile, setRemoteMediaState, setRemoteSpeaking } =
            await freshStage();
        setRemoteMediaState("a", { audio: false, video: false });
        setRemoteSpeaking("a", true);

        removeRemoteTile("a");
        addRemoteTile("a");

        expect([remoteTiles[0]?.isMuted, remoteTiles[0]?.isSpeaking]).toEqual([false, false]);
    });

    it.each([
        { speaking: true, audio: true, isSpeaking: true },
        { speaking: true, audio: false, isSpeaking: false },
        { speaking: false, audio: true, isSpeaking: false },
    ])("speaking $speaking with audio $audio gives isSpeaking $isSpeaking", async ({ speaking, audio, isSpeaking }) => {
        const { addRemoteTile, remoteTiles, setRemoteMediaState, setRemoteSpeaking } = await freshStage();
        addRemoteTile("a");

        setRemoteSpeaking("a", true);
        setRemoteSpeaking("a", speaking);
        setRemoteMediaState("a", { audio, video: true });

        expect(remoteTiles[0]?.isSpeaking).toBe(isSpeaking);
    });

    it("stops speaking when the person stops", async () => {
        const { addRemoteTile, remoteTiles, setRemoteSpeaking } = await freshStage();
        addRemoteTile("a");
        setRemoteSpeaking("a", true);

        setRemoteSpeaking("a", false);

        expect(remoteTiles[0]?.isSpeaking).toBe(false);
    });

    it.each([
        {
            name: "a live picture with a frame",
            muted: false,
            readyState: "live",
            ready: 2,
            announced: true,
            shows: true,
        },
        { name: "an announced-off camera", muted: false, readyState: "live", ready: 2, announced: false, shows: false },
        { name: "a muted track", muted: true, readyState: "live", ready: 2, announced: true, shows: false },
        { name: "an ended track", muted: false, readyState: "ended", ready: 2, announced: true, shows: false },
        { name: "no frame yet", muted: false, readyState: "live", ready: 1, announced: true, shows: false },
    ])("shows the video for $name: $shows", async ({ muted, readyState, ready, announced, shows }) => {
        const { addRemoteTile, registerRemoteVideo, remoteTiles, setRemoteMediaState } = await freshStage();
        addRemoteTile("a");
        registerRemoteVideo("a", videoShowing(streamOf(fakeTrack("video", { muted, readyState })), ready));

        setRemoteMediaState("a", { audio: true, video: announced });

        expect(remoteTiles[0]?.showsVideo).toBe(shows);
    });

    it("shows no video for a picture without a stream", async () => {
        const { addRemoteTile, registerRemoteVideo, remoteTiles, setRemoteMediaState } = await freshStage();
        addRemoteTile("a");
        registerRemoteVideo("a", videoShowing(null, 2));

        setRemoteMediaState("a", { audio: true, video: true });

        expect(remoteTiles[0]?.showsVideo).toBe(false);
    });

    it("refreshes the picture when the video loads data", async () => {
        const { addRemoteTile, registerRemoteVideo, remoteTiles } = await freshStage();
        addRemoteTile("a");
        const video = videoShowing(streamOf(fakeTrack("video")), 2);
        registerRemoteVideo("a", video);

        video.dispatchEvent(new Event("loadeddata"));

        expect(remoteTiles[0]?.showsVideo).toBe(true);
    });

    it("leaves a missing video or tile alone when refreshing", async () => {
        const { addRemoteTile, refreshPictureReady, registerRemoteVideo, remoteTiles } = await freshStage();
        addRemoteTile("a");
        registerRemoteVideo("b", videoShowing(streamOf(fakeTrack("video")), 2));

        refreshPictureReady("a");
        refreshPictureReady("b");

        expect(remoteTiles[0]?.showsVideo).toBe(false);
    });

    it("forgets the video and its listener when it is unregistered", async () => {
        const { addRemoteTile, registerRemoteVideo, remoteTiles, remoteVideoOf } = await freshStage();
        addRemoteTile("a");
        const video = videoShowing(streamOf(fakeTrack("video")), 2);

        registerRemoteVideo("a", video)();
        video.dispatchEvent(new Event("loadeddata"));

        expect([remoteVideoOf("a"), remoteTiles[0]?.showsVideo]).toEqual([undefined, false]);
    });
});

describe("agent tiles", () => {
    const tile = { name: "Game master", activity: "waiting" } as const;
    const dialed = { name: "Claude", activity: "present", sponsor: "Ada" } as const;

    it("CALL-47: an agent tile adds one tile beside the people on stage", async () => {
        const { addRemoteTile, showAgentTile, stageTileCount } = await freshStage();
        for (const person of ["a", "b", "c"]) addRemoteTile(person);
        const peopleOnly = stageTileCount();

        showAgentTile(tile);

        expect([peopleOnly, stageTileCount()]).toEqual([4, 5]);
    });

    it("CALL-47: shows every agent tile it is told beside four people, seven tiles with the quiz master and two dialed-in agents", async () => {
        const { addRemoteTile, showAgentTile, stageTileCount } = await freshStage();
        for (const person of ["a", "b", "c"]) addRemoteTile(person);

        showAgentTile(tile);
        showAgentTile(dialed, "agent-one");
        showAgentTile({ ...dialed, name: "Codex" }, "agent-two");

        expect(stageTileCount()).toBe(7);
    });

    it("CALL-47: leaves the participant count to people", async () => {
        vi.resetModules();
        const { showAgentTile } = await import("#lib/ui/stage.svelte.ts");
        const { participantCount, showParticipantCount } = await import("#lib/ui/status.svelte.ts");
        showParticipantCount(3);

        showAgentTile(tile);
        showAgentTile(dialed, "agent-one");

        expect(participantCount.count).toBe(3);
    });

    it("holds each tile under its key in the order shown until null takes it away", async () => {
        const { agentTiles, showAgentTile, stageTileCount } = await freshStage();

        showAgentTile(tile);
        showAgentTile(dialed, "agent-one");
        const shown = [...agentTiles.values()];
        showAgentTile(null);
        showAgentTile(null, "agent-one");

        expect([shown, agentTiles.size, stageTileCount()]).toEqual([[tile, dialed], 0, 1]);
    });

    it("shows a tile shown again under its key in place", async () => {
        const { agentTiles, showAgentTile } = await freshStage();

        showAgentTile(dialed, "agent-one");
        showAgentTile({ ...dialed, name: "Renamed" }, "agent-one");

        expect([...agentTiles.values()].map((shown) => shown.name)).toEqual(["Renamed"]);
    });
});

describe("feature view on the stage", () => {
    it("holds the view a feature shows until null clears it", async () => {
        const { showStage, stageSurface } = await freshStage();
        const view = (() => undefined) as unknown as FeatureView;

        showStage(view);
        const shown = stageSurface.view;
        showStage(null);

        expect([shown, stageSurface.view]).toEqual([view, null]);
    });
});

describe("an agent tile's figure", () => {
    const PATH = "/api/participation/figures/abc";

    function drawerOf() {
        const started: {
            host: HTMLElement;
            path: string;
            show: (figure: AgentFigure) => void;
            stop: ReturnType<typeof vi.fn>;
        }[] = [];
        const drawer = (host: HTMLElement, path: string, show: (figure: AgentFigure) => void) => {
            const stop = vi.fn();
            started.push({ host, path, show, stop });
            return stop;
        };
        return { drawer, started };
    }

    it("CALL-56: shows the symbol with a ring while the drawer loads, and hides the symbol only when it is drawn", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const host = document.createElement("div");

        agentFigure(host, PATH);
        const loading = host.dataset.figure;
        started[0]?.show(AGENT_FIGURE.DRAWN);

        expect([loading, host.dataset.figure, started[0]?.host, started[0]?.path]).toEqual([
            AGENT_FIGURE.LOADING,
            AGENT_FIGURE.DRAWN,
            host,
            PATH,
        ]);
    });

    it("CALL-56: brings the symbol back when the drawer ends the figure", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const host = document.createElement("div");
        agentFigure(host, PATH);

        started[0]?.show(AGENT_FIGURE.ENDED);

        expect(host.dataset.figure).toBe(AGENT_FIGURE.ENDED);
    });

    it.each([
        ["no drawer", PATH, false],
        ["no path", undefined, true],
    ])("CALL-56: leaves the tile as it was with %s", async (_name, path, hasDrawer) => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        if (hasDrawer) drawAgentFigures(drawer);
        const host = document.createElement("div");

        agentFigure(host, path).destroy();

        expect([host.dataset.figure, started.length]).toEqual([undefined, 0]);
    });

    it("CALL-56: stops the figure once when the tile goes", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const action = agentFigure(document.createElement("div"), PATH);

        action.destroy();
        action.destroy();

        expect(started[0]?.stop).toHaveBeenCalledTimes(1);
    });

    it("CALL-56: stops the old figure and starts the new path afresh when the path changes", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const host = document.createElement("div");
        const action = agentFigure(host, PATH);
        started[0]?.show(AGENT_FIGURE.ENDED);

        action.update("/api/participation/figures/def");

        expect([started[0]?.stop.mock.calls.length, started[1]?.path, host.dataset.figure]).toEqual([
            1,
            "/api/participation/figures/def",
            AGENT_FIGURE.LOADING,
        ]);
    });

    it("CALL-56: keeps the drawn figure when the same path arrives again", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const host = document.createElement("div");
        const action = agentFigure(host, PATH);
        started[0]?.show(AGENT_FIGURE.DRAWN);

        action.update(PATH);

        expect([started.length, started[0]?.stop.mock.calls.length, host.dataset.figure]).toEqual([
            1,
            0,
            AGENT_FIGURE.DRAWN,
        ]);
    });

    it("CALL-56: takes the figure away when the path goes", async () => {
        const { agentFigure, drawAgentFigures } = await freshStage();
        const { drawer, started } = drawerOf();
        drawAgentFigures(drawer);
        const host = document.createElement("div");
        const action = agentFigure(host, PATH);

        action.update(undefined);

        expect([started[0]?.stop.mock.calls.length, started.length, host.dataset.figure]).toEqual([1, 1, undefined]);
    });
});
