import { flushSync } from "svelte";
import type { AgentTile, FeatureCore } from "#lib/call/feature-port.ts";
import type { FeatureView } from "#lib/ui/chat.svelte.ts";
import { TOAST_KIND } from "#lib/ui/toast.svelte.ts";
import { SIGNAL_TYPE } from "#lib/wire.ts";
import { install, installWith } from "./index.ts";
import { fakePlayers } from "./player.fixture.ts";

export const ME = "me";

export async function installedParticipation(installOn?: typeof install) {
    const players = fakePlayers();
    const handlers = new Map<string, (frame: never) => void>();
    const sent: unknown[] = [];
    const tiles = new Map<string, AgentTile>();
    const toasts: string[] = [];
    const lines: FeatureView[] = [];
    const moreItems: Element[] = [];
    const callEndedHooks: (() => void)[] = [];
    const microphoneHandlers: ((stream: MediaStream | null) => void)[] = [];
    const more = document.createElement("dialog");
    more.open = true;
    document.body.append(more);
    const core: FeatureCore = {
        on: (type, handler) => void handlers.set(type, handler),
        send: (frame) => void sent.push(frame),
        isConnected: () => true,
        showToast: (text) => void toasts.push(text),
        toastKind: TOAST_KIND,
        showStage: () => undefined,
        drawAgentFigures: () => undefined,
        closeMore: () => undefined,
        showAgentTile: (tile, key = "agent") => {
            if (tile === null) tiles.delete(key);
            else tiles.set(key, tile);
        },
        addMoreItems: (...items) => {
            moreItems.push(...items);
            more.append(...items);
        },
        receiveChatLine: (view: FeatureView) => void lines.push(view),
        pinLastChatLine: () => undefined,
        showChatCard: () => undefined,
        onMicrophone: (handler) => {
            microphoneHandlers.push(handler);
            handler(null);
        },
        onCallEnded: (hook) => void callEndedHooks.push(hook),
    };
    await (installOn ?? ((on) => installWith(on, players.createPlayer)))(core);
    // What the page draws follows its state at the next flush.
    const onThePage = () => {
        flushSync();
        return moreItems;
    };
    const emit = (type: string, frame: object) => {
        const handler = handlers.get(type);
        if (handler === undefined) throw new Error(`no handler for ${type}`);
        (handler as (frame: object) => void)(frame);
    };
    return {
        emit,
        players,
        sent,
        tiles,
        toasts,
        lines,
        offers: () => onThePage().flatMap((item) => [...item.querySelectorAll("li")]),
        buttons: () => onThePage().flatMap((item) => [...item.querySelectorAll("button")]),
        alert: () =>
            onThePage()
                .flatMap((item) => [...item.querySelectorAll("[role=alert]")])
                .at(0)?.textContent,
        shown: () => onThePage().some((item) => item.textContent !== ""),
        sentence: () =>
            onThePage()
                .flatMap((item) => [...item.querySelectorAll("p")])
                .at(0)?.textContent,
        focused: () => moreItems.find((item) => item.contains(document.activeElement)),
        endCall: () => {
            callEndedHooks.forEach((hook) => {
                hook();
            });
        },
        hear: (stream: MediaStream | null) => {
            microphoneHandlers.forEach((handler) => {
                handler(stream);
            });
        },
        seat: (isHost: boolean, signal: string = SIGNAL_TYPE.ROOM_CREATED) => {
            emit(signal, { participant_id: ME, is_host: isHost });
        },
    };
}
